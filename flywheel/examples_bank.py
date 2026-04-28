"""
Flywheel component: Validated examples bank.

Manages data/validated_examples.jsonl — a growing set of human-validated
classifications used as few-shot examples for LLM calls.

The flywheel mechanic:
  - Every time a human validates or overrides a classification (via the review
    queue), the validated example is appended to validated_examples.jsonl.
  - On subsequent pipeline runs, call_llm() calls load_examples_for_prompt()
    automatically (auto_inject_examples=True) and prepend these examples to
    the prompt, giving the model curated in-context learning signal.
  - This compounds: the more companies are reviewed, the better the classifier
    performs on similar companies without any code changes.

JSONL format (one JSON object per line):
    {
        "prompt_name": "classifier",      // which prompt these examples apply to
        "input": { ... },                 // the company record sent to the LLM
        "output": { ... },                // the validated/corrected LLM output
        "note": "...",                    // optional human annotation
        "validated_at": "2025-01-01T...", // ISO 8601 UTC
        "validator": "isaiah"             // who validated this
    }

Current status: STUB — load_examples_for_prompt() returns [] until Step 11.
The function signature is stable; llm/client.py calls it via lazy import.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_examples_for_prompt(prompt_name: str) -> list[dict]:
    """Return validated few-shot examples for the given prompt name.

    Reads data/validated_examples.jsonl and filters by prompt_name.

    Args:
        prompt_name: e.g. "classifier", "enricher", "houston_presence"

    Returns:
        List of example dicts with "input", "output", and optional "note" keys.
        Returns [] if the file doesn't exist or no examples match the prompt.

    Note:
        STUB — returns [] until full implementation in Step 11.
    """
    # Full implementation (Step 11) will:
    #   1. Read data/validated_examples.jsonl line by line
    #   2. Filter to lines where prompt_name matches
    #   3. Return up to N most recent examples (configurable)
    return []


def append_example(
    prompt_name: str,
    input_data: dict,
    output_data: dict,
    note: str = "",
    validator: str = "pipeline",
) -> None:
    """Append a validated example to data/validated_examples.jsonl.

    Called by the review queue when a human validates or overrides a classification.
    Thread-safe via line-append (JSONL format is append-only by design).

    Args:
        prompt_name: The prompt this example applies to (e.g. "classifier").
        input_data:  The input dict sent to the LLM for this company.
        output_data: The validated/corrected output dict.
        note:        Optional human annotation explaining the validation.
        validator:   Who validated this example (username or "pipeline").

    Note:
        STUB — no-op until Step 11.
    """
    # Full implementation (Step 11) will:
    #   1. Build the example record (+ validated_at timestamp)
    #   2. Append as a single JSON line to validated_examples.jsonl
    #   3. Log the append so the run report captures flywheel growth
    logger.debug(
        f"[flywheel:stub] append_example called for {prompt_name} — no-op until Step 11"
    )
