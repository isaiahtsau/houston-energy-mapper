"""
Tests for flywheel/validated_examples.py.

All tests use a tmp_path fixture for file I/O — no writes to the real
data/validated_examples.jsonl. Zero LLM calls.

Tests:
  1. append_example: writes correct JSONL line to file
  2. load_examples: reads back what was written, returns ValidatedExample objects
  3. load_examples max_n: caps at requested limit
  4. load_examples relevance filtering: same-source examples ranked first
  5. to_few_shot_format: produces correct {input, output, note} shape
  6. load_for_classify: returns list[dict] in few-shot format filtered by source
  7. load_examples file does not exist: returns []
  8. ValidatedExample schema: review_round < 1 rejected; invalid ISO date rejected
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from flywheel.validated_examples import (
    ValidatedExample,
    append_example,
    load_examples,
    load_for_classify,
    to_few_shot_format,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _make_example(
    company_id: str = "kanin-energy",
    company_name: str = "Kanin Energy",
    source: str = "Rice Energy Tech Venture Forum (ETVF)",
    original_tier: str = "BORDERLINE",
    validated_tier: str = "VENTURE_SCALE",
    score: float = 8.0,
    sub_sector: str | None = "waste_heat_recovery",
    review_round: int = 1,
) -> ValidatedExample:
    return ValidatedExample(
        company_id=company_id,
        company_name=company_name,
        company_record={
            "name": company_name,
            "description": "Waste heat recovery technology.",
            "source": source,
            "canonical_domain": f"{company_id}.com",
        },
        original_classification={"tier": original_tier, "score": 5.0, "confidence": "LOW"},
        validated_classification={
            "tier": validated_tier,
            "score": score,
            "confidence": "HIGH",
            "sub_sector": sub_sector,
        },
        reviewer_reason="Clear venture-scale waste heat recovery technology.",
        reviewed_at=datetime.now(timezone.utc).isoformat(),
        review_round=review_round,
    )


# ── Test 1: append writes correct JSONL ───────────────────────────────────────

def test_append_example_writes_jsonl(tmp_path: Path) -> None:
    """append_example writes one valid JSON line to the file."""
    jsonl_path = tmp_path / "validated_examples.jsonl"
    ex = _make_example()

    with patch("flywheel.validated_examples._get_path", return_value=jsonl_path):
        append_example(ex)

    lines = jsonl_path.read_text().strip().split("\n")
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["company_id"] == "kanin-energy"
    assert data["company_name"] == "Kanin Energy"
    assert data["review_round"] == 1
    assert data["validated_classification"]["tier"] == "VENTURE_SCALE"


# ── Test 2: load_examples round-trips what was written ────────────────────────

def test_load_examples_round_trip(tmp_path: Path) -> None:
    """load_examples returns ValidatedExample objects matching what was appended."""
    jsonl_path = tmp_path / "validated_examples.jsonl"

    with patch("flywheel.validated_examples._get_path", return_value=jsonl_path):
        append_example(_make_example("company-a", "Company A"))
        append_example(_make_example("company-b", "Company B"))
        results = load_examples()

    assert len(results) == 2
    names = {ex.company_name for ex in results}
    assert names == {"Company A", "Company B"}
    assert all(isinstance(ex, ValidatedExample) for ex in results)


# ── Test 3: max_n cap ─────────────────────────────────────────────────────────

def test_load_examples_max_n(tmp_path: Path) -> None:
    """load_examples returns at most max_n examples."""
    jsonl_path = tmp_path / "validated_examples.jsonl"

    with patch("flywheel.validated_examples._get_path", return_value=jsonl_path):
        for i in range(10):
            append_example(_make_example(f"company-{i}", f"Company {i}"))
        results = load_examples(max_n=3)

    assert len(results) == 3


# ── Test 4: relevance filtering — same source ranked first ────────────────────

def test_load_examples_same_source_ranked_first(tmp_path: Path) -> None:
    """Examples from the same source as requested are returned before others."""
    jsonl_path = tmp_path / "validated_examples.jsonl"
    target_source = "Rice Energy Tech Venture Forum (ETVF)"

    with patch("flywheel.validated_examples._get_path", return_value=jsonl_path):
        # Add 4 non-matching, then 2 matching
        for i in range(4):
            append_example(_make_example(f"edgar-{i}", f"Edgar Co {i}", source="SEC EDGAR Form D"))
        append_example(_make_example("etvf-1", "ETVF Co 1", source=target_source))
        append_example(_make_example("etvf-2", "ETVF Co 2", source=target_source))

        results = load_examples(max_n=3, source=target_source)

    # First 2 should be the ETVF-source examples
    etvf_results = [ex for ex in results if ex.company_record.get("source") == target_source]
    assert len(etvf_results) == 2
    assert results[0].company_record["source"] == target_source
    assert results[1].company_record["source"] == target_source


# ── Test 5: to_few_shot_format shape ─────────────────────────────────────────

def test_to_few_shot_format_shape() -> None:
    """to_few_shot_format returns a dict with input, output, note keys."""
    ex = _make_example()
    result = to_few_shot_format(ex)

    assert "input" in result
    assert "output" in result
    assert "note" in result
    assert result["input"]["name"] == "Kanin Energy"
    assert result["input"]["source"] == "Rice Energy Tech Venture Forum (ETVF)"
    assert result["output"]["tier"] == "VENTURE_SCALE"
    assert result["output"]["score"] == 8.0
    assert "venture-scale" in result["note"].lower()


# ── Test 6: load_for_classify returns few-shot dicts ─────────────────────────

def test_load_for_classify_returns_few_shot_format(tmp_path: Path) -> None:
    """load_for_classify returns list[dict] with input/output/note keys."""
    jsonl_path = tmp_path / "validated_examples.jsonl"
    source = "Greentown Houston"

    with patch("flywheel.validated_examples._get_path", return_value=jsonl_path):
        append_example(_make_example("gt-1", "GT Co 1", source=source))
        append_example(_make_example("gt-2", "GT Co 2", source=source))

        results = load_for_classify({"source": source}, max_n=4)

    assert len(results) == 2
    assert all("input" in r and "output" in r for r in results)


# ── Test 7: file does not exist → empty list ──────────────────────────────────

def test_load_examples_missing_file(tmp_path: Path) -> None:
    """load_examples returns [] when validated_examples.jsonl doesn't exist."""
    missing = tmp_path / "nonexistent.jsonl"
    with patch("flywheel.validated_examples._get_path", return_value=missing):
        results = load_examples()
    assert results == []


# ── Test 8: schema validation ─────────────────────────────────────────────────

def test_validated_example_review_round_must_be_positive() -> None:
    """review_round < 1 raises ValidationError."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ValidatedExample(
            company_id="x",
            company_name="X",
            company_record={},
            original_classification={},
            validated_classification={},
            reviewer_reason="test",
            reviewed_at=datetime.now(timezone.utc).isoformat(),
            review_round=0,  # invalid
        )


def test_validated_example_invalid_iso_date() -> None:
    """reviewed_at must be a valid ISO datetime string."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ValidatedExample(
            company_id="x",
            company_name="X",
            company_record={},
            original_classification={},
            validated_classification={},
            reviewer_reason="test",
            reviewed_at="not-a-date",
            review_round=1,
        )
