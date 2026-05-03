"""
Founder extraction enrichment — LLM-based name and background extraction.

Extracts founder names and brief background signals from company profile text
(description, summary, venture_scale_reasoning), cross-referenced against the
BE Fellows lookup for confirmed names.

Scope: all 744 main-sheet records (enrichment_status='enriched', not
pending_description, not off_thesis). Records with pending_description are
skipped and assigned the placeholder "Pending Phase 2 enrichment".

Output is persisted to companies.founder_names_detail (JSON column, added on
first run via ALTER TABLE IF NOT EXISTS pattern) and companies.founder_names
(comma-separated display string).

Public API:
    extract_founders(company_id, name, description, summary, reasoning,
                     be_fellows_matches) -> FounderExtractionResult
    format_for_spreadsheet(result) -> str
    run_founder_extraction(conn, *, dry_run, force) -> ExtractionSummary
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from enrich.be_fellows_lookup import lookup_company_for_fellow_match
from llm.client import call_llm

logger = logging.getLogger(__name__)

# ── Response schema ────────────────────────────────────────────────────────────

class FounderRecord(BaseModel):
    name: str
    role: str
    background_signals: str = ""


class FounderExtractionResult(BaseModel):
    founders: list[FounderRecord] = Field(default_factory=list)
    extraction_confidence: Literal["HIGH", "MEDIUM", "LOW"] = "LOW"
    extraction_notes: str = ""


# ── Helpers ────────────────────────────────────────────────────────────────────

_PLACEHOLDER_PENDING = "Pending Phase 2 enrichment"
_PLACEHOLDER_NOT_SURFACED = "Names not surfaced from harvested sources."


def _build_be_fellows_context(be_fellows_matches: list[dict]) -> str:
    """Format BE Fellows matches into the prompt context string."""
    if not be_fellows_matches:
        return "None"
    parts = []
    for m in be_fellows_matches:
        role_str = m.get("role", "BE Fellow")
        parts.append(f"{m['name']} ({role_str}) — BE Fellow, confirmed")
    return "; ".join(parts)


def _be_fellows_as_founders(be_fellows_matches: list[dict]) -> list[FounderRecord]:
    """Convert confirmed BE Fellows matches directly to FounderRecord objects."""
    records = []
    for m in be_fellows_matches:
        role = m.get("role", "Co-founder")
        if not role or role.lower() in ("business fellow", ""):
            role = "Co-founder"
        records.append(FounderRecord(
            name=m["name"],
            role=role,
            background_signals="BE Fellow",
        ))
    return records


# ── Core extraction ────────────────────────────────────────────────────────────

def extract_founders(
    company_id: str,
    name: str,
    description: str,
    summary: str,
    reasoning: str,
    be_fellows_matches: list[dict] | None = None,
    *,
    prompt_version: str = "v1",
    dry_run: bool = False,
) -> FounderExtractionResult:
    """Extract founders for a single company via LLM + BE Fellows cross-reference.

    Args:
        company_id:          Pipeline company ID (for logging).
        name:                Company name.
        description:         Raw harvested description text.
        summary:             Enriched summary.
        reasoning:           venture_scale_reasoning text.
        be_fellows_matches:  Pre-computed BE Fellows matches (or None to run lookup).
        prompt_version:      Prompt version to use (default "v1").
        dry_run:             If True, skip the API call and return a placeholder result.

    Returns:
        FounderExtractionResult with founders list, confidence, and notes.
    """
    if be_fellows_matches is None:
        be_fellows_matches = lookup_company_for_fellow_match(name)

    be_context = _build_be_fellows_context(be_fellows_matches)

    # Skip LLM if all text is empty and no BE Fellows
    all_text = " ".join(filter(None, [description, summary, reasoning]))
    if len(all_text.strip()) < 10 and not be_fellows_matches:
        return FounderExtractionResult(
            founders=[],
            extraction_confidence="LOW",
            extraction_notes=_PLACEHOLDER_NOT_SURFACED,
        )

    if dry_run:
        # Return a minimal result without API call
        if be_fellows_matches:
            return FounderExtractionResult(
                founders=_be_fellows_as_founders(be_fellows_matches),
                extraction_confidence="HIGH",
                extraction_notes="BE Fellows match (dry run).",
            )
        return FounderExtractionResult(
            founders=[],
            extraction_confidence="LOW",
            extraction_notes="dry_run — no LLM call made",
        )

    variables = {
        "name": name or "",
        "description": (description or "")[:1500],
        "summary": (summary or "")[:800],
        "reasoning": (reasoning or "")[:600],
        "be_fellows_context": be_context,
    }

    try:
        resp = call_llm(
            prompt_name="founder_extraction",
            prompt_version=prompt_version,
            variables=variables,
            response_schema=FounderExtractionResult,
            max_tokens=512,
            temperature=0.0,
            auto_inject_examples=False,
        )
    except Exception as exc:
        logger.warning(f"[founder_extraction:{company_id}] LLM error: {exc}")
        # Fallback: return BE Fellows if any, else empty
        if be_fellows_matches:
            return FounderExtractionResult(
                founders=_be_fellows_as_founders(be_fellows_matches),
                extraction_confidence="MEDIUM",
                extraction_notes="BE Fellows match; LLM call failed.",
            )
        return FounderExtractionResult(
            founders=[],
            extraction_confidence="LOW",
            extraction_notes=_PLACEHOLDER_NOT_SURFACED,
        )

    if resp.parsed is None:
        logger.warning(f"[founder_extraction:{company_id}] Parse failure; raw={resp.content[:100]}")
        if be_fellows_matches:
            return FounderExtractionResult(
                founders=_be_fellows_as_founders(be_fellows_matches),
                extraction_confidence="MEDIUM",
                extraction_notes="BE Fellows match; LLM parse failure.",
            )
        return FounderExtractionResult(
            founders=[],
            extraction_confidence="LOW",
            extraction_notes=_PLACEHOLDER_NOT_SURFACED,
        )

    result = resp.parsed

    # Ensure BE Fellows are always in the list, even if LLM missed them
    be_names_lower = {m["name"].lower() for m in be_fellows_matches}
    existing_names_lower = {f.name.lower() for f in result.founders}
    for bf in _be_fellows_as_founders(be_fellows_matches):
        if bf.name.lower() not in existing_names_lower:
            result.founders.insert(0, bf)

    return result


# ── Spreadsheet formatter ─────────────────────────────────────────────────────

def format_for_spreadsheet(result: FounderExtractionResult | None, *, pending: bool = False) -> str:
    """Format extraction result for the Founder Pedigree spreadsheet column.

    Args:
        result:  FounderExtractionResult, or None for pending records.
        pending: If True, return the pending placeholder regardless of result.

    Returns:
        Formatted string:
          - Pending: "Pending Phase 2 enrichment"
          - Founders found: "Name (role) - background; Name2 (role2)"
          - No founders: extraction_notes (e.g. "Names not surfaced from harvested sources.")
    """
    if pending or result is None:
        return _PLACEHOLDER_PENDING

    if not result.founders:
        return result.extraction_notes or _PLACEHOLDER_NOT_SURFACED

    parts = []
    for f in result.founders:
        entry = f"{f.name} ({f.role})"
        if f.background_signals:
            entry += f" - {f.background_signals}"
        parts.append(entry)
    return "; ".join(parts)


# ── Bulk run ──────────────────────────────────────────────────────────────────

class ExtractionSummary(BaseModel):
    total_processed: int = 0
    with_founders: int = 0
    empty_with_notes: int = 0
    be_fellows_matches: int = 0
    errors: int = 0
    total_cost_usd: float = 0.0
    samples: list[dict] = Field(default_factory=list)


def _ensure_column(conn: sqlite3.Connection) -> None:
    """Add founder_names_detail column if it doesn't exist."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(companies)").fetchall()]
    if "founder_names_detail" not in cols:
        conn.execute("ALTER TABLE companies ADD COLUMN founder_names_detail TEXT")
        conn.commit()
        logger.info("[founder_extraction] Added founder_names_detail column")


def run_founder_extraction(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    force: bool = False,
    batch_size: int = 50,
    sleep_between: float = 0.0,
) -> ExtractionSummary:
    """Run founder extraction across all 744 main-sheet (enriched) records.

    Args:
        conn:           Connection to pipeline.db.
        dry_run:        Skip LLM calls; use BE Fellows only. For cost estimation.
        force:          Re-process records that already have founder_names_detail.
        batch_size:     Commit every N records.
        sleep_between:  Seconds to sleep between LLM calls (rate limiting).

    Returns:
        ExtractionSummary with counts and sample rows.
    """
    conn.row_factory = sqlite3.Row
    _ensure_column(conn)

    # Build query — scope: enriched, not duplicate, not excluded, not off_thesis
    where_extra = "" if force else "AND (c.founder_names_detail IS NULL OR c.founder_names_detail = '')"
    rows = conn.execute(f"""
        SELECT c.id, c.name, c.summary, c.venture_scale_reasoning,
               c.venture_scale_score, c.sub_sector, c.source_ids,
               r.description
        FROM companies c
        LEFT JOIN (
            SELECT company_id, description FROM raw_records
            WHERE id IN (SELECT MAX(id) FROM raw_records GROUP BY company_id)
        ) r ON r.company_id = c.id
        WHERE c.is_duplicate=0 AND c.is_excluded=0
          AND c.enrichment_status='enriched'
          AND (c.sub_sector != 'off_thesis' OR c.sub_sector IS NULL)
          {where_extra}
        ORDER BY c.venture_scale_score DESC NULLS LAST
    """).fetchall()

    summary = ExtractionSummary()
    now = datetime.now(timezone.utc).isoformat()
    batch_updates = []

    sample_targets = {"high_score_no_founders", "be_fellows", "multi_founder",
                      "not_surfaced", "medium_score"}
    sample_collected: set[str] = set()

    logger.info(f"[founder_extraction:run] {len(rows)} records to process (dry_run={dry_run})")

    for i, row in enumerate(rows):
        company_id = row["id"]
        name = row["name"] or ""
        description = row["description"] or ""
        summary_text = row["summary"] or ""
        reasoning = row["venture_scale_reasoning"] or ""

        # BE Fellows lookup
        be_matches = lookup_company_for_fellow_match(name)

        try:
            result = extract_founders(
                company_id=company_id,
                name=name,
                description=description,
                summary=summary_text,
                reasoning=reasoning,
                be_fellows_matches=be_matches,
                dry_run=dry_run,
            )
        except Exception as exc:
            logger.error(f"[founder_extraction:{company_id}] Unexpected error: {exc}")
            summary.errors += 1
            continue

        summary.total_processed += 1

        if be_matches:
            summary.be_fellows_matches += 1

        if result.founders:
            summary.with_founders += 1
        else:
            summary.empty_with_notes += 1

        # Persist
        detail_json = result.model_dump_json()
        display_names = ", ".join(f.name for f in result.founders)
        batch_updates.append((detail_json, display_names, now, company_id))

        # Collect samples
        score = row["venture_scale_score"] or 0
        sample_label = None
        if be_matches and "be_fellows" not in sample_collected:
            sample_label = "be_fellows"
        elif len(result.founders) >= 2 and "multi_founder" not in sample_collected:
            sample_label = "multi_founder"
        elif not result.founders and "not_surfaced" not in sample_collected:
            sample_label = "not_surfaced"
        elif score >= 8.0 and result.founders and "high_score_no_founders" not in sample_collected:
            sample_label = "high_score_no_founders"
        elif 6.0 <= score < 8.0 and result.founders and "medium_score" not in sample_collected:
            sample_label = "medium_score"

        if sample_label and len(summary.samples) < 10:
            summary.samples.append({
                "label": sample_label,
                "company": name,
                "score": score,
                "sources": json.loads(row["source_ids"] or "[]"),
                "formatted": format_for_spreadsheet(result),
                "extraction_confidence": result.extraction_confidence,
                "n_founders": len(result.founders),
            })
            sample_collected.add(sample_label)

        # Batch commit
        if len(batch_updates) >= batch_size:
            conn.executemany(
                "UPDATE companies SET founder_names_detail=?, founder_names=?, last_updated_at=? WHERE id=?",
                batch_updates,
            )
            conn.commit()
            batch_updates.clear()
            logger.info(f"[founder_extraction:progress] {summary.total_processed}/{len(rows)} done")

        if sleep_between > 0 and not dry_run:
            time.sleep(sleep_between)

    # Final flush
    if batch_updates:
        conn.executemany(
            "UPDATE companies SET founder_names_detail=?, founder_names=?, last_updated_at=? WHERE id=?",
            batch_updates,
        )
        conn.commit()

    logger.info(
        f"[founder_extraction:complete] processed={summary.total_processed} "
        f"with_founders={summary.with_founders} "
        f"be_fellows={summary.be_fellows_matches} "
        f"errors={summary.errors}"
    )
    return summary
