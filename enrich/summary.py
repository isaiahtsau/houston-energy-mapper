"""
One-sentence summary generation.

Generates a concise, factual one-sentence company summary suitable for the
deliverable spreadsheet. The summary is written in the style of a venture
analyst note — technology-first, avoids marketing language.

Example output:
  "Cemvita Factory engineers microbes to convert CO₂ and methane into
   bio-based chemicals, with a pilot operating at a Houston refinery."

Status: STUB — interface defined, implementation in Step 8.
"""
from __future__ import annotations

from typing import Any


def generate_summary(
    company: dict[str, Any],
    prompt_version: str = "v1",
) -> dict[str, str]:
    """Generate a one-sentence analyst-style summary for a company.

    Args:
        company:        Company record with name, description, sub_sector, tags.
        prompt_version: Prompt version.

    Returns:
        Dict: {"summary": "...", "confidence": "HIGH"}

    Note:
        STUB — raises NotImplementedError until Step 8.
    """
    raise NotImplementedError("generate_summary — implemented in Step 8")
