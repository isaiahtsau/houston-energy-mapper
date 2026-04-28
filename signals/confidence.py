"""
Per-field confidence aggregation.

Each field in the pipeline output (venture_scale_score, houston_tier, sub_sector, etc.)
carries a confidence flag: HIGH, MEDIUM, or LOW.

Aggregation rules:
  HIGH:   Two or more corroborating independent signals.
  MEDIUM: One strong signal, or two or more weak signals.
  LOW:    Single weak signal — company is surfaced in the manual review queue.

The confidence flag is computed separately from the LLM's own self-reported
confidence, and is used to gate the review queue: LOW-confidence companies are
always queued for human review regardless of the score.

Status: STUB — interface defined, implementation during Step 6–8.
"""
from __future__ import annotations

from enum import Enum


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


def aggregate_confidence(signals: list[dict]) -> Confidence:
    """Aggregate multiple evidence signals into a single confidence level.

    Args:
        signals: List of dicts with "strength" ("HIGH"|"MEDIUM"|"LOW") and
                 "source" (name of the signal source).

    Returns:
        Aggregated Confidence enum value.

    Note:
        STUB — raises NotImplementedError until Step 6.
    """
    raise NotImplementedError("aggregate_confidence — implemented in Step 6")
