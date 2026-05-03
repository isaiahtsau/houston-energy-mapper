"""
Cross-source canonical domain coalesce.

The dedup pipeline never propagated raw_records.website → companies.canonical_domain.
This module fixes that without re-harvesting: for every company missing a
canonical_domain, it queries all raw_records rows matching by name and picks the
first non-null website value found.

Resolution priority order (most reliable sources first, based on observed website
population rates from diagnostic):
  InnovationMap RSS (0% missing) → Greentown (1.6%) → Halliburton Labs (2.4%)
  → GOOSE Capital (3.3%) → Rice ETVF (5.4%) → Ion District (29.2%)
  → RBPC Alumni (66.7%) → everything else

Within the priority order, ties are broken by MAX(raw_records.id) (most-recently
harvested row wins, which tends to be the cleanest URL).

Output:
  - Writes to companies.canonical_domain (only where currently NULL/empty)
  - Returns CoalesceSummary with per-source attribution counts and remaining nulls
  - Idempotent: re-running changes nothing if canonical_domain is already set

Public API:
    coalesce_domains(conn, *, dry_run, scope_vs_bl_only) -> CoalesceSummary
"""
from __future__ import annotations

import logging
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urlparse

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Source priority for tie-breaking when multiple rows have a website value.
# Lower index = higher priority.
_SOURCE_PRIORITY: list[str] = [
    "InnovationMap Houston RSS",
    "Greentown Houston",
    "Halliburton Labs",
    "GOOSE Capital",
    "Rice Energy Tech Venture Forum (ETVF)",
    "Ion District",
    "RBPC Alumni",
]


def _priority(source: str) -> int:
    """Return sort key for source priority (lower = preferred)."""
    try:
        return _SOURCE_PRIORITY.index(source)
    except ValueError:
        return len(_SOURCE_PRIORITY)  # unknown sources rank last


def _normalize_url(raw: str) -> str | None:
    """Return a cleaned URL string, or None if it looks invalid."""
    url = raw.strip().rstrip("/")
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        parsed = urlparse(url)
        if not parsed.netloc or "." not in parsed.netloc:
            return None
        return url
    except Exception:
        return None


# ── Summary model ──────────────────────────────────────────────────────────────

class CoalesceSummary(BaseModel):
    total_missing_before: int = 0
    resolved: int = 0
    still_null: int = 0
    by_source: dict[str, int] = Field(default_factory=dict)
    samples: list[dict] = Field(default_factory=list)
    dry_run: bool = False


# ── Core function ──────────────────────────────────────────────────────────────

def coalesce_domains(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    scope_vs_bl_only: bool = True,
) -> CoalesceSummary:
    """Propagate raw_records.website → companies.canonical_domain via name join.

    Args:
        conn:              Open connection to pipeline.db.
        dry_run:           If True, compute resolutions but do not write to DB.
        scope_vs_bl_only:  If True (default), restrict to VS+BL companies
                           (venture_scale_score >= 4.0, not off_thesis).

    Returns:
        CoalesceSummary with counts, per-source attribution, and samples.
    """
    conn.row_factory = sqlite3.Row

    scope_filter = ""
    if scope_vs_bl_only:
        scope_filter = """
            AND c.venture_scale_score >= 4.0
            AND (c.sub_sector != 'off_thesis' OR c.sub_sector IS NULL)
        """

    # Fetch all companies missing canonical_domain
    missing = conn.execute(f"""
        SELECT c.id, c.name
        FROM companies c
        WHERE c.is_duplicate = 0
          AND c.is_excluded  = 0
          AND (c.canonical_domain IS NULL OR c.canonical_domain = '')
          {scope_filter}
        ORDER BY c.name
    """).fetchall()

    summary = CoalesceSummary(
        total_missing_before=len(missing),
        dry_run=dry_run,
    )

    logger.info(
        f"[coalesce] {len(missing)} companies missing canonical_domain "
        f"(scope_vs_bl_only={scope_vs_bl_only}, dry_run={dry_run})"
    )

    by_source: dict[str, int] = defaultdict(int)
    updates: list[tuple[str, str, str]] = []  # (canonical_domain, updated_at, company_id)
    now = datetime.now(timezone.utc).isoformat()

    for row in missing:
        company_id = row["id"]
        name = row["name"] or ""

        # Fetch all raw_records rows for this company with a non-null website
        candidates = conn.execute("""
            SELECT r.source, r.website, r.id
            FROM raw_records r
            WHERE LOWER(TRIM(r.name_raw)) = LOWER(TRIM(?))
              AND r.website IS NOT NULL
              AND r.website != ''
            ORDER BY r.id DESC
        """, (name,)).fetchall()

        if not candidates:
            continue

        # Pick the highest-priority source; within same priority, highest id wins
        best = min(candidates, key=lambda r: (_priority(r["source"]), -r["id"]))
        raw_url = best["website"]
        source  = best["source"]

        url = _normalize_url(raw_url)
        if not url:
            logger.debug(f"[coalesce] {name}: skipped malformed URL '{raw_url}'")
            continue

        by_source[source] += 1
        summary.resolved += 1

        if len(summary.samples) < 20:
            summary.samples.append({
                "company": name,
                "url": url,
                "source": source,
                "n_candidates": len(candidates),
            })

        if not dry_run:
            updates.append((url, now, company_id))

        logger.debug(f"[coalesce] {name} → {url}  (via {source})")

    # Batch write
    if updates and not dry_run:
        conn.executemany(
            "UPDATE companies SET canonical_domain=?, last_updated_at=? WHERE id=?",
            updates,
        )
        conn.commit()
        logger.info(f"[coalesce] Wrote {len(updates)} canonical_domain values")

    # Count still-null after resolution
    still_null = conn.execute(f"""
        SELECT COUNT(*) FROM companies c
        WHERE c.is_duplicate = 0
          AND c.is_excluded  = 0
          AND (c.canonical_domain IS NULL OR c.canonical_domain = '')
          {scope_filter}
    """).fetchone()[0]

    if dry_run:
        # still_null in dry-run is total_missing - resolved (nothing was written)
        still_null = summary.total_missing_before - summary.resolved

    summary.still_null = still_null
    summary.by_source  = dict(by_source)

    logger.info(
        f"[coalesce] resolved={summary.resolved} "
        f"still_null={summary.still_null} "
        f"dry_run={dry_run}"
    )
    return summary
