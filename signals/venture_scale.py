"""
Venture-scale classifier.

Uses Claude to score each company candidate against the venture-scale rubric
defined in docs/venture_scale_rubric.md.

The classifier runs in two passes:
  Pass 1 — Hard-exclude rules (no LLM call):
    Services firm, consulting-only, family-run business, project SPV,
    IP-licensing-only shell. Identified from description keywords + source tags.

  Pass 2 — LLM scoring (prompts/classifier_v1.md):
    Seven rubric dimensions scored 0.0–1.0, averaged to a composite score:
      1. Technology defensibility (proprietary process/hardware/software)
      2. IP signal (patents, trade secrets, exclusive licenses)
      3. Capital-intensity profile (CapEx requirements suggesting defensibility)
      4. Customer pilot signal (paid pilots, LOIs, commercial deployments)
      5. Federal grant signal (DOE, ARPA-E, NSF, SBIR/STTR awards)
      6. Founder pedigree signal (deep domain expertise, repeat founder)
      7. Business model (product vs. services — services is a negative signal)

Output per company:
  venture_scale_score:       float 0.0–1.0
  venture_scale_confidence:  "HIGH" | "MEDIUM" | "LOW"
  venture_scale_reasoning:   free-text trace from LLM
  is_excluded:               bool (hard-exclude rules fired)
  exclude_reason:            str | None

Status: STUB — interface defined, implementation in Step 6.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class VentureScaleResult:
    """Output of classify_venture_scale() for a single company."""
    company_id: str
    venture_scale_score: float          # 0.0–1.0; None if hard-excluded
    venture_scale_confidence: str       # "HIGH" | "MEDIUM" | "LOW"
    venture_scale_reasoning: str        # LLM reasoning trace
    is_excluded: bool                   # True if hard-exclude rules fired
    exclude_reason: str | None          # e.g. "services firm — no technology IP"
    prompt_version: str                 # e.g. "v1"
    call_id: str | None                 # LLM call UUID for log correlation


def classify_venture_scale(
    company: dict[str, Any],
    prompt_version: str = "v1",
) -> VentureScaleResult:
    """Classify a company against the venture-scale rubric.

    Args:
        company:        Dict with name, description, website, tags, extra fields.
        prompt_version: Prompt version to use (e.g. "v1"). Increment when prompt changes.

    Returns:
        VentureScaleResult with score, confidence, reasoning, and exclude flags.

    Note:
        STUB — raises NotImplementedError until Step 6.
    """
    raise NotImplementedError("classify_venture_scale — implemented in Step 6")
