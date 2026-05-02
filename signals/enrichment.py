"""
Step 8 enrichment — sub-sector classification, summary generation, and
founder pedigree scoring for all classified, non-excluded companies.

Three LLM passes per company:
  1. classify_sub_sector  — maps company to primary_sector + sub_sector
  2. generate_summary     — produces 2–3 sentence analyst-grade summary
  3. score_description_pedigree — augmented founder pedigree from description text
     (calls enrich.founder_pedigree.score_founder_pedigree with LLM augmentation)

Idempotency: enrich_company() checks which columns are still NULL before
calling the LLM and only runs passes for missing data. A partial run (e.g.
network interruption mid-batch) can resume without redundant LLM calls.

Public API:
  enrich_company(company_id, name, conn) -> EnrichmentResult
  get_enrich_targets(conn) -> list[tuple[str, str]]   # (id, name) pairs
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel

from enrich.founder_pedigree import (
    FounderPedigree,
    score_founder_pedigree,
)

logger = logging.getLogger(__name__)

# Module-level import so tests can patch signals.enrichment.call_llm.
try:
    from llm.client import call_llm
except Exception:  # pragma: no cover
    call_llm = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Sub-sector controlled vocabulary
# ---------------------------------------------------------------------------

# Canonical mapping from sub_sector → primary_sector.
# This is the single source of truth; the prompt references this structure.
_SUB_SECTOR_TO_PRIMARY: dict[str, str] = {
    # traditional_energy
    "oil_gas_software":             "traditional_energy",
    "oilfield_services_tech":       "traditional_energy",
    "lng_infrastructure":           "traditional_energy",
    # energy_transition
    "green_hydrogen":               "energy_transition",
    "blue_hydrogen":                "energy_transition",
    "carbon_capture_utilization_storage": "energy_transition",
    "geothermal":                   "energy_transition",
    "battery_storage":              "energy_transition",
    "grid_modernization":           "energy_transition",
    "solar":                        "energy_transition",
    "wind":                         "energy_transition",
    "nuclear":                      "energy_transition",
    "methane_abatement":            "energy_transition",
    "sustainable_fuels":            "energy_transition",
    "water_energy_nexus":           "energy_transition",
    "energy_efficiency":            "energy_transition",
    # industrial_tech
    "industrial_decarbonization":   "industrial_tech",
    "energy_data_analytics":        "industrial_tech",
    "advanced_materials":           "industrial_tech",
    "manufacturing_ai":             "industrial_tech",
    # special
    "off_thesis":                   "off_thesis",
    "unknown":                      "energy_transition",  # fallback for thin descriptions
}

_VALID_PRIMARY_SECTORS = frozenset({
    "traditional_energy", "energy_transition", "industrial_tech", "off_thesis",
})


# ---------------------------------------------------------------------------
# Pydantic output schemas
# ---------------------------------------------------------------------------


class SubSectorResult(BaseModel):
    """Output of the sub-sector classification LLM pass."""

    company_id: str
    primary_sector: str   # validated against _VALID_PRIMARY_SECTORS after parsing
    sub_sector: str
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    reasoning: str


class SummaryResult(BaseModel):
    """Output of the summary generation LLM pass."""

    company_id: str
    summary: Optional[str]   # None when description is too thin; stored as NULL
    confidence: Literal["HIGH", "MEDIUM", "LOW"]


# ---------------------------------------------------------------------------
# Input container
# ---------------------------------------------------------------------------


@dataclass
class EnrichInput:
    """Per-company enrichment input assembled from raw_records."""

    company_id: str
    name: str
    description: str    # best available description; "[no description available]" if absent


@dataclass
class EnrichmentResult:
    """Result of enriching one company (all three passes)."""

    company_id: str
    sub_sector: SubSectorResult
    summary: SummaryResult
    pedigree: FounderPedigree


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def get_enrich_targets(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Return (company_id, name) pairs that still need at least one enrichment column filled.

    Companies are ordered by venture_scale_score DESC so the highest-value
    records are enriched first if the run is interrupted.
    """
    rows = conn.execute(
        """
        SELECT id, name FROM companies
        WHERE is_excluded = 0
          AND venture_scale_score IS NOT NULL
          AND (
              sub_sector IS NULL
              OR summary IS NULL
              OR founder_pedigree_score IS NULL
          )
        ORDER BY venture_scale_score DESC
        """
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def _best_description(company_id: str, name: str, conn: sqlite3.Connection) -> str:
    """Return the longest non-null description from raw_records for this company.

    raw_records.company_id is NULL until cross-source dedup (Step 10) runs.
    Match by LOWER(name_raw) = LOWER(name) as a proxy — this works because
    companies.name is set from raw_records.name_raw during the classify stage.
    Falls back to company_id match for future-proofing after dedup is done.
    """
    row = conn.execute(
        """
        SELECT description FROM raw_records
        WHERE (company_id = ? OR LOWER(name_raw) = LOWER(?))
          AND description IS NOT NULL AND length(description) > 0
        ORDER BY length(description) DESC
        LIMIT 1
        """,
        (company_id, name),
    ).fetchone()
    return row[0] if row else "[no description available]"


def _needs_enrichment(company_id: str, conn: sqlite3.Connection) -> dict[str, bool]:
    """Return which of the three enrichment passes still need to run for this company."""
    row = conn.execute(
        "SELECT sub_sector, summary, founder_pedigree_score FROM companies WHERE id = ?",
        (company_id,),
    ).fetchone()
    if row is None:
        return {"sub_sector": True, "summary": True, "pedigree": True}
    return {
        "sub_sector": row[0] is None,
        "summary": row[1] is None,
        "pedigree": row[2] is None,
    }


# ---------------------------------------------------------------------------
# Pass 1: Sub-sector classification
# ---------------------------------------------------------------------------


def classify_sub_sector(record: EnrichInput) -> SubSectorResult:
    """Call LLM to classify company into primary_sector + sub_sector.

    Falls back to sub_sector="unknown", primary_sector="energy_transition",
    confidence="LOW" on any LLM failure.
    """
    _FALLBACK = SubSectorResult(
        company_id=record.company_id,
        primary_sector="energy_transition",
        sub_sector="unknown",
        confidence="LOW",
        reasoning="LLM call failed or returned unparseable response.",
    )

    if call_llm is None:  # pragma: no cover
        return _FALLBACK

    try:
        resp = call_llm(
            prompt_name="sub_sector",
            prompt_version="v1",
            variables={
                "company_id": record.company_id,
                "name": record.name,
                "description": record.description,
            },
            response_schema=SubSectorResult,
            model="claude-haiku-4-5",
            max_tokens=200,
            temperature=0.0,
        )
    except Exception as exc:
        logger.warning(f"[enrichment:sub_sector-error] {record.company_id}: {exc}")
        return _FALLBACK

    if resp.parsed is None:
        logger.warning(f"[enrichment:sub_sector-parse-fail] {record.company_id}")
        return _FALLBACK

    result: SubSectorResult = resp.parsed  # type: ignore[assignment]

    # Correct primary_sector if LLM returned an inconsistent value
    canonical_primary = _SUB_SECTOR_TO_PRIMARY.get(result.sub_sector)
    if canonical_primary and result.primary_sector != canonical_primary:
        logger.debug(
            f"[enrichment:sub_sector-primary-correction] {record.company_id}: "
            f"LLM returned primary={result.primary_sector!r} for sub={result.sub_sector!r}; "
            f"corrected to {canonical_primary!r}"
        )
        result = SubSectorResult(
            company_id=result.company_id,
            primary_sector=canonical_primary,
            sub_sector=result.sub_sector,
            confidence=result.confidence,
            reasoning=result.reasoning,
        )

    # Validate primary_sector is in known set
    if result.primary_sector not in _VALID_PRIMARY_SECTORS:
        result = SubSectorResult(
            company_id=result.company_id,
            primary_sector="off_thesis",
            sub_sector=result.sub_sector,
            confidence="LOW",
            reasoning=result.reasoning,
        )

    return result


# ---------------------------------------------------------------------------
# Pass 2: Summary generation
# ---------------------------------------------------------------------------


def generate_summary(record: EnrichInput) -> SummaryResult:
    """Call LLM to generate a 2–3 sentence analyst-grade summary.

    Returns summary=None (stored as SQL NULL) when description is too thin.
    Falls back to summary=None on any LLM failure.
    """
    _FALLBACK = SummaryResult(
        company_id=record.company_id,
        summary=None,
        confidence="LOW",
    )

    if call_llm is None:  # pragma: no cover
        return _FALLBACK

    try:
        resp = call_llm(
            prompt_name="summary",
            prompt_version="v1",
            variables={
                "company_id": record.company_id,
                "name": record.name,
                "description": record.description,
            },
            response_schema=SummaryResult,
            model="claude-haiku-4-5",
            max_tokens=300,
            temperature=0.3,
        )
    except Exception as exc:
        logger.warning(f"[enrichment:summary-error] {record.company_id}: {exc}")
        return _FALLBACK

    if resp.parsed is None:
        logger.warning(f"[enrichment:summary-parse-fail] {record.company_id}")
        return _FALLBACK

    return resp.parsed  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Pass 3: Founder pedigree (description-based)
# ---------------------------------------------------------------------------


def score_description_pedigree(record: EnrichInput) -> FounderPedigree:
    """Run the full founder pedigree scoring pipeline against the company description.

    Since we don't have individual founder records (names, roles), we treat the
    company description as the bio_text. The deterministic detectors + LLM
    augmentation operate on whatever pedigree signals appear in the text.

    The resulting FounderPedigree uses name="[description]" and role="Other"
    as placeholders — these are not surfaced in the UI, only the score/tier/confidence
    and the full audit object matter for DB storage.
    """
    return score_founder_pedigree(
        founder_name="[description]",
        bio_text=record.description,
        role="Other",
        company_id=record.company_id,
        company_licensed_ip_labs=[],
        is_solo_founder=False,
        has_technical_cofounder=True,
    )


# ---------------------------------------------------------------------------
# Orchestration: enrich one company
# ---------------------------------------------------------------------------


def enrich_company(
    company_id: str,
    name: str,
    conn: sqlite3.Connection,
) -> EnrichmentResult:
    """Run all three enrichment passes for one company and write results to DB.

    Idempotent: each pass checks whether its target column is already populated
    and skips the LLM call if so. A partial run can resume safely.

    Args:
        company_id: companies.id primary key.
        name:       Display name for logging.
        conn:       SQLite connection (caller manages transaction scope).

    Returns:
        EnrichmentResult with the outputs of all three passes.
    """
    description = _best_description(company_id, name, conn)
    record = EnrichInput(company_id=company_id, name=name, description=description)
    needs = _needs_enrichment(company_id, conn)

    sub_result: SubSectorResult | None = None
    summ_result: SummaryResult | None = None
    pedigree_result: FounderPedigree | None = None

    if needs["sub_sector"]:
        sub_result = classify_sub_sector(record)
        logger.debug(
            f"[enrichment:sub_sector] {name!r}: "
            f"{sub_result.primary_sector}/{sub_result.sub_sector} ({sub_result.confidence})"
        )

    if needs["summary"]:
        summ_result = generate_summary(record)
        summary_preview = (
            (summ_result.summary or "")[:60] + "…"
            if summ_result.summary and len(summ_result.summary) > 60
            else summ_result.summary or "(null)"
        )
        logger.debug(f"[enrichment:summary] {name!r}: {summary_preview!r}")

    if needs["pedigree"]:
        pedigree_result = score_description_pedigree(record)
        logger.debug(
            f"[enrichment:pedigree] {name!r}: "
            f"score={pedigree_result.final_score} tier={pedigree_result.tier}"
        )

    # Build update payload — only set columns that were (re-)computed this run
    updates: dict[str, object] = {}
    now = datetime.now(timezone.utc).isoformat()

    if sub_result is not None:
        updates["sub_sector"] = sub_result.sub_sector
        updates["primary_sector"] = sub_result.primary_sector

    if summ_result is not None:
        updates["summary"] = summ_result.summary  # may be None → SQL NULL

    if pedigree_result is not None:
        updates["founder_pedigree_score"] = pedigree_result.final_score
        updates["founder_pedigree_tier"] = pedigree_result.tier
        updates["founder_pedigree_confidence"] = pedigree_result.confidence
        updates["founder_pedigree_full"] = pedigree_result.model_dump_json()

    if updates:
        updates["last_updated_at"] = now
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [company_id]
        conn.execute(
            f"UPDATE companies SET {set_clause} WHERE id = ?",
            values,
        )
        conn.commit()

    # Return results (using fallback stubs if a pass was skipped due to idempotency)
    # Fetch current DB values for skipped passes so the return object is always complete
    if sub_result is None or summ_result is None or pedigree_result is None:
        row = conn.execute(
            """SELECT sub_sector, primary_sector, summary,
                      founder_pedigree_score, founder_pedigree_tier,
                      founder_pedigree_confidence, founder_pedigree_full
               FROM companies WHERE id = ?""",
            (company_id,),
        ).fetchone()
        if sub_result is None and row:
            sub_result = SubSectorResult(
                company_id=company_id,
                primary_sector=row[1] or "energy_transition",
                sub_sector=row[0] or "unknown",
                confidence="HIGH",  # already validated on prior run
                reasoning="(loaded from DB — previously enriched)",
            )
        if summ_result is None and row:
            summ_result = SummaryResult(
                company_id=company_id,
                summary=row[2],
                confidence="HIGH",
            )
        if pedigree_result is None and row:
            # Reconstruct a minimal FounderPedigree from stored flat columns
            raw_fp = row[6]
            if raw_fp:
                try:
                    pedigree_result = FounderPedigree.model_validate_json(raw_fp)
                except Exception:
                    pass
            if pedigree_result is None:
                pedigree_result = score_description_pedigree(record)

    # Final fallbacks (should never be needed in practice)
    if sub_result is None:
        sub_result = SubSectorResult(
            company_id=company_id, primary_sector="energy_transition",
            sub_sector="unknown", confidence="LOW", reasoning="",
        )
    if summ_result is None:
        summ_result = SummaryResult(company_id=company_id, summary=None, confidence="LOW")
    if pedigree_result is None:
        pedigree_result = score_description_pedigree(record)

    return EnrichmentResult(
        company_id=company_id,
        sub_sector=sub_result,
        summary=summ_result,
        pedigree=pedigree_result,
    )
