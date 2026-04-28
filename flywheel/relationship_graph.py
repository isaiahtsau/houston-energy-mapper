"""
Flywheel component: Relationship graph.

Maintains a SQLite graph of founder, investor, and accelerator affiliations.
When a new company is classified, prior-signal is computed from its affiliations:
  - Founders who founded other already-classified Houston energy companies get
    a positive prior (likely legitimate Houston energy startup)
  - Companies backed by known Houston energy investors (ECV, Greentown, etc.)
    get a prior that boosts their Houston presence score

Database: data/db/relationship_graph.db (schema defined in __init__ block below)

Status: STUB — interface defined, implementation in Step 11.
"""
from __future__ import annotations


def add_affiliation(
    company_id: str,
    affiliation_type: str,  # "founder" | "investor" | "accelerator" | "university"
    affiliation_name: str,
    detail: str = "",
) -> None:
    """Record an affiliation between a company and a named entity.

    Note: STUB — no-op until Step 11.
    """
    pass


def get_prior_signal(company_id: str) -> float:
    """Return a prior signal score [0.0–1.0] based on graph affiliations.

    Note: STUB — returns 0.0 until Step 11.
    """
    return 0.0
