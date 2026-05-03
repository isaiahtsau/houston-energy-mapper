"""
Flywheel component: Validated examples schema and I/O.

Manages data/validated_examples.jsonl — a growing set of human-validated
classifications used as few-shot examples for LLM classifier calls.

The flywheel mechanic:
  - Every time a human validates or overrides a classification during Step 12
    manual review, append_example() writes the record to validated_examples.jsonl.
  - On subsequent pipeline runs, load_examples() reads the file and filters to
    the most-relevant examples for the company being classified (same source,
    similar score range, similar sub_sector).
  - These examples are injected as few-shot context into the LLM prompt, giving
    the model curated in-context learning signal without any prompt rewriting.
  - This compounds: more reviews → better classifier on similar companies.

JSONL format (one JSON object per line):
    {
        "company_id": "kanin-energy",
        "company_name": "Kanin Energy",
        "company_record": {"name": ..., "description": ..., "source": ...},
        "original_classification": {"tier": "BORDERLINE", "score": 5.0, "confidence": "LOW"},
        "validated_classification": {"tier": "VENTURE_SCALE", "score": 8.0, "confidence": "HIGH"},
        "reviewer_reason": "Waste heat recovery is clearly venture-scale; missed by classifier due to sparse description.",
        "reviewed_at": "2025-05-02T12:00:00+00:00",
        "review_round": 1
    }

Public API:
    append_example(example: ValidatedExample) -> None
    load_examples(*, max_n, source, score_range, sub_sector) -> list[ValidatedExample]
    to_few_shot_format(example: ValidatedExample) -> dict
    load_for_classify(company_record, max_n) -> list[dict]
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator

logger = logging.getLogger(__name__)

# Resolved lazily to avoid import-time dependency on config module
_JSONL_PATH: Path | None = None


def _get_path() -> Path:
    global _JSONL_PATH
    if _JSONL_PATH is None:
        from config.settings import settings
        _JSONL_PATH = settings.validated_examples_path
    return _JSONL_PATH


# ── Schema ─────────────────────────────────────────────────────────────────────

class ValidatedExample(BaseModel):
    """A single human-validated classification override.

    Stored as one JSON line in data/validated_examples.jsonl.
    Loaded and injected as few-shot context into LLM classifier calls.
    """
    company_id: str
    company_name: str
    company_record: dict           # name, description, source, canonical_domain, etc.
    original_classification: dict  # tier, score, confidence from first automated run
    validated_classification: dict # tier, score, confidence after human review
    reviewer_reason: str           # 1-2 sentences explaining the override decision
    reviewed_at: str               # ISO 8601 UTC timestamp
    review_round: int              # 1 for Step 12 first pass; 2+ for subsequent rounds

    @field_validator("review_round")
    @classmethod
    def review_round_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("review_round must be >= 1")
        return v

    @field_validator("reviewed_at")
    @classmethod
    def reviewed_at_iso(cls, v: str) -> str:
        # Accept any ISO string; just ensure it parses
        datetime.fromisoformat(v)
        return v


# ── File I/O ───────────────────────────────────────────────────────────────────

def append_example(example: ValidatedExample) -> None:
    """Append a validated example to data/validated_examples.jsonl.

    Called by the review queue when a human validates or overrides a classification.
    JSONL append-only format is inherently thread-safe for single-writer use.

    Args:
        example: A fully-populated ValidatedExample.
    """
    path = _get_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(example.model_dump_json() + "\n")
    logger.info(
        "[flywheel] appended example for '%s' (round=%d, validated=%s)",
        example.company_name,
        example.review_round,
        example.validated_classification.get("tier"),
    )


def load_examples(
    *,
    max_n: int = 8,
    source: str | None = None,
    score_range: tuple[float, float] | None = None,
    sub_sector: str | None = None,
) -> list[ValidatedExample]:
    """Load validated examples from disk, optionally filtered by relevance.

    Args:
        max_n:       Maximum number of examples to return. Most recently reviewed
                     examples are preferred (file is in append order).
        source:      If provided, prefer examples from this source. Exact-source
                     matches are ranked first; others are included to fill max_n.
        score_range: (low, high) inclusive. Examples whose validated score falls
                     outside this range are deprioritized (still included if needed
                     to fill max_n after source-preferred examples).
        sub_sector:  If provided, prefer examples with this validated sub_sector.

    Returns:
        Up to max_n ValidatedExample objects. Empty list if file doesn't exist.
    """
    path = _get_path()
    if not path.exists():
        return []

    all_examples: list[ValidatedExample] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                all_examples.append(ValidatedExample.model_validate(data))
            except (json.JSONDecodeError, Exception) as exc:
                logger.warning("[flywheel] skipping invalid line %d: %s", lineno, exc)

    if not all_examples:
        return []

    # Score each example by relevance (higher = more relevant)
    def _relevance(ex: ValidatedExample) -> tuple[int, int, int]:
        source_match = int(
            source is not None
            and ex.company_record.get("source") == source
        )
        score_match = int(
            score_range is not None
            and score_range[0] <= (ex.validated_classification.get("score") or 0.0) <= score_range[1]
        )
        sub_sector_match = int(
            sub_sector is not None
            and ex.validated_classification.get("sub_sector") == sub_sector
        )
        return (source_match, score_match, sub_sector_match)

    # Stable sort: most-recently-appended first (reverse file order), then by relevance
    # Most recent = highest relevance for ties
    indexed = list(enumerate(all_examples))  # (original_index, example)
    indexed.sort(key=lambda t: (_relevance(t[1]), t[0]), reverse=True)

    return [ex for _, ex in indexed[:max_n]]


# ── Format conversion ──────────────────────────────────────────────────────────

def to_few_shot_format(example: ValidatedExample) -> dict:
    """Convert a ValidatedExample to the {input, output} format used by call_llm().

    The 'input' is the company record fields; the 'output' is the validated
    classification with an optional reviewer_reason note.

    Returns:
        {"input": {...}, "output": {...}, "note": "..."}
    """
    return {
        "input": {
            "company_id": example.company_id,
            "name": example.company_record.get("name", example.company_name),
            "description": example.company_record.get("description", ""),
            "website": example.company_record.get("canonical_domain", ""),
            "source": example.company_record.get("source", ""),
        },
        "output": example.validated_classification,
        "note": example.reviewer_reason,
    }


def load_for_classify(
    company_record: dict[str, Any],
    max_n: int = 6,
) -> list[dict]:
    """Load and format relevant examples for a classify_venture_scale call.

    Convenience wrapper used by classify_venture_scale when examples_bank is None.
    Filters by source, then by score range 4.0–9.0 (the full VS+BORDERLINE band),
    then by sub_sector if available.

    Args:
        company_record: Dict with at least "source" key. Usually the
                        CompanyRecord model's dict form.
        max_n:          Cap on returned examples.

    Returns:
        List of {input, output, note} dicts ready for few-shot injection.
        Empty list if validated_examples.jsonl doesn't exist or is empty.
    """
    source = company_record.get("source")
    examples = load_examples(
        max_n=max_n,
        source=source,
        score_range=(4.0, 9.0),
        sub_sector=company_record.get("sub_sector"),
    )
    return [to_few_shot_format(ex) for ex in examples]
