"""
Flywheel component: Validated examples bank.

Manages data/validated_examples.jsonl — a growing set of human-validated
classifications used as few-shot examples for LLM calls.

The flywheel mechanic:
  - Every time a human validates or overrides a classification (via the review
    queue), the validated example is appended to validated_examples.jsonl.
  - On subsequent pipeline runs, call_llm() calls load_examples_for_prompt()
    automatically (auto_inject_examples=True) and prepends these examples to
    the prompt, giving the model curated in-context learning signal.
  - This compounds: the more companies are reviewed, the better the classifier
    performs on similar companies without any code changes.

JSONL format (one JSON object per line):
    {
        "company_id": "kanin-energy",
        "company_name": "Kanin Energy",
        "company_record": {"name": ..., "description": ..., "source": ...},
        "original_classification": {"tier": "BORDERLINE", "score": 5.0, ...},
        "validated_classification": {"tier": "VENTURE_SCALE", "score": 8.0, ...},
        "reviewer_reason": "Waste heat recovery is clearly venture-scale...",
        "reviewed_at": "2025-05-02T12:00:00+00:00",
        "review_round": 1
    }

This module is the entry point called by llm/client.py via lazy import.
The richer ValidatedExample schema and relevance filtering live in
flywheel/validated_examples.py.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def load_examples_for_prompt(prompt_name: str) -> list[dict]:
    """Return validated few-shot examples for the given prompt name.

    Reads data/validated_examples.jsonl, filters to lines where prompt_name
    matches or defaults to 'classifier' when not specified, and returns up to
    8 most-recently-reviewed examples in {input, output, note} format.

    Called by llm/client.py _load_flywheel_examples() with auto_inject_examples=True.
    For relevance-filtered loading (same source, score range, sub_sector), use
    flywheel.validated_examples.load_for_classify() directly.

    Args:
        prompt_name: e.g. "classifier", "enricher", "houston_presence"

    Returns:
        List of {"input": ..., "output": ..., "note": ...} dicts.
        Returns [] if the file doesn't exist or no examples match.
    """
    from flywheel.validated_examples import load_examples, to_few_shot_format

    examples = load_examples(max_n=8)
    return [to_few_shot_format(ex) for ex in examples]


def append_example(
    prompt_name: str,
    input_data: dict,
    output_data: dict,
    note: str = "",
    validator: str = "pipeline",
) -> None:
    """Append a validated example to data/validated_examples.jsonl.

    Legacy interface used by older call sites. Prefer using
    flywheel.validated_examples.append_example(ValidatedExample(...)) directly
    for new code (richer schema with original_classification, review_round, etc.).

    Args:
        prompt_name: The prompt this example applies to (e.g. "classifier").
        input_data:  The input dict sent to the LLM for this company.
        output_data: The validated/corrected output dict.
        note:        Optional human annotation explaining the validation.
        validator:   Who validated this example (username or "pipeline").
    """
    from flywheel.validated_examples import ValidatedExample, append_example as _append

    now = datetime.now(timezone.utc).isoformat()
    company_id = input_data.get("company_id", "unknown")
    company_name = input_data.get("name", "unknown")

    example = ValidatedExample(
        company_id=company_id,
        company_name=company_name,
        company_record=input_data,
        original_classification={},
        validated_classification=output_data,
        reviewer_reason=note or f"Validated by {validator}",
        reviewed_at=now,
        review_round=1,
    )
    _append(example)
