"""
Sub-sector classification enrichment.

Assigns each company a sub-sector label from the Houston energy ecosystem taxonomy.
Used in the spreadsheet for filtering and sorting.

Example sub-sectors:
  Carbon Capture & Storage, Green Hydrogen, Industrial Electrification,
  Energy Storage, Grid Technology, Upstream O&G Tech, Midstream Tech,
  Nuclear (SMR/Fusion), Climate Intelligence / Analytics, Industrial Biotech,
  Water & Waste in Energy, Energy Efficiency / Building Tech, Other

Status: STUB — interface defined, implementation in Step 8.
"""
from __future__ import annotations

from typing import Any


def classify_sub_sector(
    company: dict[str, Any],
    prompt_version: str = "v1",
) -> dict[str, str]:
    """Classify a company into an energy ecosystem sub-sector.

    Args:
        company:        Company record with name, description, tags, summary.
        prompt_version: Prompt version.

    Returns:
        Dict: {"sub_sector": "Carbon Capture & Storage", "confidence": "HIGH"}

    Note:
        STUB — raises NotImplementedError until Step 8.
    """
    raise NotImplementedError("classify_sub_sector — implemented in Step 8")
