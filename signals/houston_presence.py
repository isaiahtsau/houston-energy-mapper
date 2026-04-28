"""
Houston presence scorer.

Assigns each company a tier (A / A-low / B-high / B / B-low / C) based on a
composite signal score. Every score is accompanied by a per-signal contribution
trace so it is fully auditable.

Signal weights (from docs/houston_presence_signals.md):
  HIGH   = 3 pts: Form D Houston address, Texas SOS formation in Harris/Fort Bend/
                  Montgomery/Brazoria/Galveston/Waller county, ERCOT IA signed in
                  Houston load zone, Houston accelerator residency, DOE hub sub-awardee
  MEDIUM = 2 pts: Houston office on company website, Houston investor lead,
                  job postings at Houston address
  LOW    = 1 pt:  Founder LinkedIn shows Houston, press mention of Houston operations,
                  Houston listed on Crunchbase/pitchbook (single-source)

Tier rules:
  A or A-low:  ≥6 points AND ≥1 HIGH operational signal
  B-high:      ≥6 points but no HIGH operational signal (strong soft signals only)
  B:           3–5 points
  B-low:       1–2 points (review queue — may be PR-only presence)
  C:           0 points (no credible Houston signal)

This module is built in Step 4 — the first substantive implementation after scaffolding.
See tests/signals/test_houston_presence.py for the test suite.

Status: STUB — interface defined, implementation in Step 4.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class HoustonTier(str, Enum):
    """Enumerated Houston presence tiers."""
    A = "A"
    A_LOW = "A-low"
    B_HIGH = "B-high"
    B = "B"
    B_LOW = "B-low"
    C = "C"


@dataclass
class SignalContribution:
    """A single signal's contribution to the Houston presence score.

    Included in the per-company trace written to the database and spreadsheet.
    """
    signal_name: str        # e.g. "form_d_houston_address"
    signal_category: str    # "HIGH" | "MEDIUM" | "LOW"
    points: int             # 3 | 2 | 1
    source: str             # where this signal was detected (e.g. "sec_edgar_form_d")
    detail: str             # human-readable evidence string


@dataclass
class PresenceScore:
    """Output of score_houston_presence() for a single company.

    Attributes:
        tier:               The assigned Houston presence tier.
        points:             Total composite score.
        has_high_operational: True if ≥1 HIGH signal is operational (not PR-only).
        contributions:      Ordered list of signal contributions (highest points first).
        confidence:         "HIGH" | "MEDIUM" | "LOW" based on corroborating signals.
    """
    tier: HoustonTier
    points: int
    has_high_operational: bool
    contributions: list[SignalContribution]
    confidence: str


def score_houston_presence(company: dict[str, Any]) -> PresenceScore:
    """Score a company's Houston presence and return a tiered result with trace.

    Args:
        company: Dict with any combination of:
          - form_d_address: str | None        (SEC EDGAR Form D address)
          - sos_state: str | None             (Texas SOS formation state)
          - sos_county: str | None            (Texas SOS county)
          - ercot_project: bool               (ERCOT IA-signed project in Houston zone)
          - accelerator_houston: str | None   (Houston accelerator residency)
          - doe_hub_subawardee: bool          (DOE hub sub-awardee)
          - website_houston_office: bool      (Houston office on company website)
          - houston_investor_lead: str | None (Houston-based lead investor)
          - job_postings_houston: int         (count of Houston job postings)
          - founder_linkedin_houston: bool    (founder LinkedIn shows Houston)
          - press_mentions_houston: int       (count of press mentions of Houston ops)

    Returns:
        PresenceScore with tier, points, trace, and confidence.

    Note:
        STUB — raises NotImplementedError until Step 4.
    """
    raise NotImplementedError("score_houston_presence — implemented in Step 4")
