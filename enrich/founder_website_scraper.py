"""
Founder website scraper — fetches About/Team pages for founder extraction.

For each VENTURE_SCALE or BORDERLINE record (venture_scale_score >= 4.0,
is_duplicate=0, is_excluded=0, sub_sector != 'off_thesis', enrichment_status=
'enriched'), resolves the company website from raw_records via name join,
attempts to fetch standard About/Team paths, strips HTML, and passes cleaned
text to the LLM for founder extraction.

Results are written to companies.founder_names_detail (overwrites prior
text-only extraction) and companies.founder_names (comma-separated display).

Domain resolution: companies.canonical_domain is NULL for all records.
Website is resolved via raw_records.website using a name join:
  LOWER(TRIM(r.name_raw)) = LOWER(TRIM(c.name))

Placeholder strings:
  - No website:               "No website available"
  - Fetch failed / blocked:   "Company website not publicly accessible"
  - No founders on site:      "Founders not listed on company website"

Public API:
    fetch_team_page(base_url, *, timeout, min_chars) -> tuple[str | None, str]
    scrape_website_for_founders(company_id, name, base_url, ...) -> FounderExtractionResult
    run_website_scraper(conn, *, dry_run, force, stop_on_failure_rate) -> ScrapeSummary
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from datetime import datetime, timezone
from typing import Literal

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from enrich.founder_extraction import (
    FounderExtractionResult,
    FounderRecord,
    _be_fellows_as_founders,
    _PLACEHOLDER_NOT_SURFACED,
)
from enrich.be_fellows_lookup import lookup_company_for_fellow_match
from llm.client import call_llm

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_PLACEHOLDER_NO_WEBSITE = "No website available"
_PLACEHOLDER_NOT_ACCESSIBLE = "Company website not publicly accessible"
_PLACEHOLDER_NOT_LISTED = "Founders not listed on company website"

_TEAM_PATHS = [
    "/about",
    "/about-us",
    "/team",
    "/our-team",
    "/leadership",
    "/people",
    "/our-people",
    "/company",
    "/our-story",
    "/who-we-are",
    "/founders",
    "/management",
    "/about/team",
    "/about/leadership",
    "/",  # homepage last resort — only used if all others fail
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

_MAX_TEXT_CHARS = 8000
_MIN_TEXT_CHARS = 500
_DEFAULT_TIMEOUT = 10


# ── HTML fetching ──────────────────────────────────────────────────────────────

def _normalize_base_url(raw_url: str) -> str:
    """Strip trailing slash, ensure scheme is present."""
    url = raw_url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _strip_html(html: str) -> str:
    """Strip HTML tags and collapse whitespace, capped at _MAX_TEXT_CHARS."""
    soup = BeautifulSoup(html, "html.parser")
    # Remove script, style, nav, footer noise
    for tag in soup(["script", "style", "nav", "footer", "head"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text[:_MAX_TEXT_CHARS]


def fetch_team_page(
    base_url: str,
    *,
    timeout: int = _DEFAULT_TIMEOUT,
    min_chars: int = _MIN_TEXT_CHARS,
    paths: list[str] | None = None,
    session: requests.Session | None = None,
) -> tuple[str | None, str]:
    """Attempt to fetch an About/Team page from a company website.

    Tries candidate paths in priority order, stopping at the first successful
    response with more than min_chars of text content.

    Args:
        base_url:   Company website base URL (scheme + domain, no trailing slash).
        timeout:    HTTP timeout in seconds per request.
        min_chars:  Minimum stripped-text length to accept a page as valid.
        paths:      Override the default candidate paths.
        session:    Optional requests.Session for connection reuse (and mocking).

    Returns:
        (fetched_url, stripped_text) if a valid page was found.
        (None, "") if all paths failed or returned insufficient content.
    """
    effective_paths = paths if paths is not None else _TEAM_PATHS
    requester = session or requests

    base = _normalize_base_url(base_url)

    for path in effective_paths:
        url = base + path
        try:
            resp = requester.get(url, headers=_HEADERS, timeout=timeout, allow_redirects=True)
            if resp.status_code != 200:
                continue
            text = _strip_html(resp.text)
            if len(text) >= min_chars:
                logger.debug(f"[website_scraper] Found page: {url} ({len(text)} chars)")
                return url, text
        except requests.exceptions.Timeout:
            logger.debug(f"[website_scraper] Timeout: {url}")
            continue
        except requests.exceptions.RequestException as exc:
            logger.debug(f"[website_scraper] Request error {url}: {exc}")
            continue

    return None, ""


# ── LLM extraction ─────────────────────────────────────────────────────────────

def scrape_website_for_founders(
    company_id: str,
    name: str,
    base_url: str,
    *,
    be_fellows_matches: list[dict] | None = None,
    prompt_version: str = "v1",
    dry_run: bool = False,
    session: requests.Session | None = None,
) -> tuple[FounderExtractionResult, str | None]:
    """Fetch a company's About/Team page and extract founders via LLM.

    Args:
        company_id:         Pipeline company ID (for logging).
        name:               Company name.
        base_url:           Company website base URL.
        be_fellows_matches: Pre-computed BE Fellows matches (or None to run lookup).
        prompt_version:     Prompt version (default "v1").
        dry_run:            If True, skip fetch and LLM call; return placeholder result.
        session:            Optional requests.Session (for testing).

    Returns:
        (FounderExtractionResult, fetched_url | None)
        fetched_url is the URL of the page actually used, or None if fetch failed.
    """
    if be_fellows_matches is None:
        be_fellows_matches = lookup_company_for_fellow_match(name)

    if dry_run:
        if be_fellows_matches:
            return (
                FounderExtractionResult(
                    founders=_be_fellows_as_founders(be_fellows_matches),
                    extraction_confidence="HIGH",
                    extraction_notes="BE Fellows match (dry run).",
                ),
                None,
            )
        return (
            FounderExtractionResult(
                founders=[],
                extraction_confidence="LOW",
                extraction_notes="dry_run — no fetch or LLM call made",
            ),
            None,
        )

    fetched_url, page_text = fetch_team_page(base_url, session=session)

    if not fetched_url or not page_text:
        # Fetch failed — return BE Fellows if available, else not-accessible placeholder
        if be_fellows_matches:
            return (
                FounderExtractionResult(
                    founders=_be_fellows_as_founders(be_fellows_matches),
                    extraction_confidence="MEDIUM",
                    extraction_notes="BE Fellows match; website not accessible.",
                ),
                None,
            )
        return (
            FounderExtractionResult(
                founders=[],
                extraction_confidence="LOW",
                extraction_notes=_PLACEHOLDER_NOT_ACCESSIBLE,
            ),
            None,
        )

    variables = {
        "company_name": name,
        "page_url": fetched_url,
        "page_text": page_text,
        "be_fellows_context": (
            "; ".join(
                f"{m['name']} ({m.get('role', 'BE Fellow')}) — confirmed"
                for m in be_fellows_matches
            ) if be_fellows_matches else "None"
        ),
    }

    try:
        resp = call_llm(
            prompt_name="founder_website",
            prompt_version=prompt_version,
            variables=variables,
            response_schema=FounderExtractionResult,
            max_tokens=512,
            temperature=0.0,
            auto_inject_examples=False,
        )
    except Exception as exc:
        logger.warning(f"[website_scraper:{company_id}] LLM error: {exc}")
        if be_fellows_matches:
            return (
                FounderExtractionResult(
                    founders=_be_fellows_as_founders(be_fellows_matches),
                    extraction_confidence="MEDIUM",
                    extraction_notes="BE Fellows match; LLM call failed.",
                ),
                fetched_url,
            )
        return (
            FounderExtractionResult(
                founders=[],
                extraction_confidence="LOW",
                extraction_notes=_PLACEHOLDER_NOT_LISTED,
            ),
            fetched_url,
        )

    if resp.parsed is None:
        logger.warning(
            f"[website_scraper:{company_id}] Parse failure; raw={resp.content[:100]}"
        )
        if be_fellows_matches:
            return (
                FounderExtractionResult(
                    founders=_be_fellows_as_founders(be_fellows_matches),
                    extraction_confidence="MEDIUM",
                    extraction_notes="BE Fellows match; LLM parse failure.",
                ),
                fetched_url,
            )
        return (
            FounderExtractionResult(
                founders=[],
                extraction_confidence="LOW",
                extraction_notes=_PLACEHOLDER_NOT_LISTED,
            ),
            fetched_url,
        )

    result = resp.parsed

    # Always ensure BE Fellows are in the list, even if LLM missed them
    existing_names_lower = {f.name.lower() for f in result.founders}
    for bf in _be_fellows_as_founders(be_fellows_matches):
        if bf.name.lower() not in existing_names_lower:
            result.founders.insert(0, bf)

    return result, fetched_url


# ── Bulk run ───────────────────────────────────────────────────────────────────

class ScrapeSummary(BaseModel):
    total_candidates: int = 0
    no_website: int = 0
    fetch_attempted: int = 0
    fetch_success: int = 0
    fetch_failed: int = 0
    with_founders: int = 0
    empty_results: int = 0
    be_fellows_matches: int = 0
    errors: int = 0
    total_cost_usd: float = 0.0
    samples: list[dict] = Field(default_factory=list)


def run_website_scraper(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    force: bool = False,
    batch_size: int = 25,
    sleep_between: float = 0.5,
    stop_on_failure_rate: float = 0.5,
    timeout: int = _DEFAULT_TIMEOUT,
) -> ScrapeSummary:
    """Run the website scraper across all VS+BL enriched records.

    Args:
        conn:                    Connection to pipeline.db.
        dry_run:                 Skip fetch and LLM calls.
        force:                   Re-process records that already have founder_names_detail.
        batch_size:              Commit every N records.
        sleep_between:           Seconds between HTTP fetch attempts.
        stop_on_failure_rate:    Abort if fetch failure rate exceeds this fraction.
        timeout:                 HTTP request timeout per path attempt.

    Returns:
        ScrapeSummary with counts and sample rows.
    """
    conn.row_factory = sqlite3.Row

    where_extra = (
        ""
        if force
        else "AND (c.founder_names_detail IS NULL OR c.founder_names_detail = '' OR c.founder_names_detail NOT LIKE '%fetched_url%')"
    )

    # Resolve websites via name join (canonical_domain is NULL for all records)
    rows = conn.execute(f"""
        SELECT c.id, c.name, c.venture_scale_score, c.sub_sector, c.source_ids,
               c.summary, c.venture_scale_reasoning, c.founder_names_detail,
               r.website
        FROM companies c
        LEFT JOIN (
            SELECT name_raw, MAX(id) as max_id FROM raw_records
            GROUP BY LOWER(TRIM(name_raw))
        ) dedup ON LOWER(TRIM(c.name)) = LOWER(TRIM(dedup.name_raw))
        LEFT JOIN raw_records r ON r.id = dedup.max_id
        WHERE c.is_duplicate=0 AND c.is_excluded=0
          AND c.enrichment_status='enriched'
          AND c.venture_scale_score >= 4.0
          AND (c.sub_sector != 'off_thesis' OR c.sub_sector IS NULL)
          {where_extra}
        ORDER BY c.venture_scale_score DESC NULLS LAST
    """).fetchall()

    summary = ScrapeSummary(total_candidates=len(rows))
    now = datetime.now(timezone.utc).isoformat()
    batch_updates: list[tuple] = []

    sample_collected: set[str] = set()

    logger.info(
        f"[website_scraper:run] {len(rows)} candidates "
        f"(dry_run={dry_run}, force={force})"
    )

    session = requests.Session()

    for i, row in enumerate(rows):
        company_id = row["id"]
        name = row["name"] or ""
        website = row["website"] or ""
        score = row["venture_scale_score"] or 0.0

        if not website or website.strip() == "":
            summary.no_website += 1
            # Persist placeholder so we don't re-attempt
            batch_updates.append((
                '{"founders":[],"extraction_confidence":"LOW",'
                '"extraction_notes":"No website available"}',
                "",
                now,
                company_id,
            ))
            if len(batch_updates) >= batch_size:
                _flush(conn, batch_updates, now)
                batch_updates.clear()
                logger.info(
                    f"[website_scraper:progress] {i+1}/{len(rows)} done"
                )
            continue

        summary.fetch_attempted += 1

        # BE Fellows lookup
        be_matches = lookup_company_for_fellow_match(name)
        if be_matches:
            summary.be_fellows_matches += 1

        try:
            result, fetched_url = scrape_website_for_founders(
                company_id=company_id,
                name=name,
                base_url=website,
                be_fellows_matches=be_matches,
                dry_run=dry_run,
                session=session,
            )
        except Exception as exc:
            logger.error(f"[website_scraper:{company_id}] Unexpected error: {exc}")
            summary.errors += 1
            continue

        if fetched_url:
            summary.fetch_success += 1
        else:
            summary.fetch_failed += 1
            # Check early abort threshold
            if summary.fetch_attempted >= 10:
                failure_rate = summary.fetch_failed / summary.fetch_attempted
                if failure_rate >= stop_on_failure_rate:
                    logger.error(
                        f"[website_scraper:abort] Fetch failure rate {failure_rate:.1%} "
                        f">= threshold {stop_on_failure_rate:.1%}. "
                        f"Processed {summary.fetch_attempted} domains. Aborting."
                    )
                    break

        if result.founders:
            summary.with_founders += 1
        else:
            summary.empty_results += 1

        # Persist: store result JSON with fetched_url embedded for force-detection
        import json as _json
        detail_dict = result.model_dump()
        if fetched_url:
            detail_dict["fetched_url"] = fetched_url
        detail_json = _json.dumps(detail_dict)
        display_names = ", ".join(f.name for f in result.founders)
        batch_updates.append((detail_json, display_names, now, company_id))

        # Collect samples
        sample_label = None
        if result.founders and be_matches and "be_fellows" not in sample_collected:
            sample_label = "be_fellows"
        elif len(result.founders) >= 2 and "multi_founder" not in sample_collected:
            sample_label = "multi_founder"
        elif result.founders and "single_founder" not in sample_collected:
            sample_label = "single_founder"
        elif not result.founders and fetched_url and "page_no_founders" not in sample_collected:
            sample_label = "page_no_founders"
        elif not fetched_url and "fetch_failed" not in sample_collected:
            sample_label = "fetch_failed"
        elif score >= 8.0 and result.founders and "high_score" not in sample_collected:
            sample_label = "high_score"

        if sample_label and len(summary.samples) < 12:
            summary.samples.append({
                "label": sample_label,
                "company": name,
                "score": score,
                "website": website,
                "fetched_url": fetched_url,
                "n_founders": len(result.founders),
                "confidence": result.extraction_confidence,
                "founders_preview": display_names[:120] if display_names else "",
                "notes": result.extraction_notes[:80] if result.extraction_notes else "",
            })
            sample_collected.add(sample_label)

        # Batch commit
        if len(batch_updates) >= batch_size:
            _flush(conn, batch_updates, now)
            batch_updates.clear()
            logger.info(
                f"[website_scraper:progress] {i+1}/{len(rows)} done "
                f"(fetched={summary.fetch_success}, failed={summary.fetch_failed})"
            )

        if sleep_between > 0 and not dry_run and fetched_url:
            time.sleep(sleep_between)

    # Final flush
    if batch_updates:
        _flush(conn, batch_updates, now)

    logger.info(
        f"[website_scraper:complete] "
        f"candidates={summary.total_candidates} "
        f"no_website={summary.no_website} "
        f"fetch_success={summary.fetch_success} "
        f"fetch_failed={summary.fetch_failed} "
        f"with_founders={summary.with_founders} "
        f"errors={summary.errors}"
    )
    return summary


def _flush(conn: sqlite3.Connection, updates: list[tuple], _now: str) -> None:
    conn.executemany(
        "UPDATE companies SET founder_names_detail=?, founder_names=?, last_updated_at=? WHERE id=?",
        updates,
    )
    conn.commit()
