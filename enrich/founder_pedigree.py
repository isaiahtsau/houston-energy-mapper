"""
Founder pedigree enrichment.

For each company that passes the venture-scale filter, this module:
  1. Extracts founder names from the company description and website
  2. Classifies each founder against the Houston founder pedigree taxonomy
     (docs/founder_pedigree_taxonomy.md) using Claude
  3. Returns a structured pedigree dict: {name: {tier, detail, confidence}}

Pedigree tiers (from docs/founder_pedigree_taxonomy.md — to be provided in Step 3):
  TIER_1: Deep domain expertise + prior venture exit or Fortune 500 C-suite
  TIER_2: Domain expertise (PhD, 10+ years sector experience) or prior startup
  TIER_3: Domain adjacent (energy adjacent, adjacent sector)
  TIER_4: No clear domain signal from available data

Status: STUB — interface defined, implementation in Step 8.
"""
from __future__ import annotations

from typing import Any


def enrich_founder_pedigree(
    company: dict[str, Any],
    prompt_version: str = "v1",
) -> dict[str, Any]:
    """Extract and classify founder pedigree for a company.

    Args:
        company:        Company record with name, description, website, extra.
        prompt_version: Prompt version (increments as taxonomy is refined).

    Returns:
        Dict: {
            "founder_names": ["Alice Smith", "Bob Lee"],
            "founder_pedigree": {
                "Alice Smith": {"tier": "TIER_1", "detail": "...", "confidence": "HIGH"},
                ...
            }
        }

    Note:
        STUB — raises NotImplementedError until Step 8.
    """
    raise NotImplementedError("enrich_founder_pedigree — implemented in Step 8")
