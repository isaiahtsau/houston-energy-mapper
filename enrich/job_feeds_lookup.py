"""
Job feeds enrichment lookup — Greenhouse, Lever, and Ashby ATS APIs.

Per-company query to detect open roles, with a focus on Houston-area positions.
Used during enrichment to determine whether a company is actively hiring locally
— signal `active_houston_hiring`.

Access pattern (per live-site inspection 2026-05-02):
  Greenhouse:
    API: GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
    Returns JSON with jobs list; no auth required (public job boards only).
    Rate limit: no documented limit; treat as polite (1 req/s).
    Response: {"jobs": [{"id": ..., "title": "...", "location": {"name": "..."}, ...}]}

  Lever:
    API: GET https://api.lever.co/v0/postings/{company}?mode=json
    Returns JSON array of postings; no auth required (public postings only).
    Response: [{"id": "...", "text": "...", "categories": {"location": "..."}, ...}]

  Ashby:
    API: POST https://api.ashbyhq.com/posting-api/job-board/{slug}
    Body: {"limit": 100}
    Returns JSON with jobs; no auth required (public job boards only).
    Response: {"results": [{"id": ..., "title": "...", "location": "...", ...}]}

The lookup tries each configured ATS in order until one succeeds.
Houston detection: location string contains "houston" (case-insensitive) or
  is blank/remote (treated as unknown, not Houston).

Company → ATS slug mapping: this module maintains a hardcoded reference mapping
for known Houston energy/climate companies. For unknown companies, the slug is
derived by lowercasing, removing non-alphanumeric characters, and trying each ATS.

Public API:
  lookup_job_feeds(company_name) -> JobFeedsResult
"""
from __future__ import annotations

import logging
import re
from typing import TypedDict

import requests

logger = logging.getLogger(__name__)

_GREENHOUSE_URL = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
_LEVER_URL = "https://api.lever.co/v0/postings/{slug}?mode=json"
_ASHBY_URL = "https://api.ashbyhq.com/posting-api/job-board/{slug}"

_HEADERS = {
    "User-Agent": "IonTakeHome research@ion.com",
    "Accept": "application/json",
}

_TIMEOUT = 10  # seconds

# Houston location keywords (case-insensitive match on location string)
_HOUSTON_KEYWORDS = frozenset({
    "houston", "katy", "sugar land", "the woodlands", "pearland",
    "pasadena", "friendswood", "league city", "baytown", "stafford",
    "galveston", "conroe",
})


class JobFeedsResult(TypedDict):
    found: bool             # True if any ATS returned a valid response
    platform: str | None    # "greenhouse", "lever", "ashby", or None
    slug: str | None        # slug used for the successful lookup
    total_jobs: int         # total open roles across all locations
    houston_jobs: int       # roles with a Houston-area location string
    houston_job_titles: list[str]  # titles of Houston-area open roles (up to 10)
    remote_jobs: int        # roles with "remote" in location (unknown geography)


def _slugify(company_name: str) -> str:
    """Derive a likely ATS slug from a company name.

    Lowercases, replaces spaces/punctuation with hyphens, strips leading/trailing
    hyphens, and collapses consecutive hyphens. e.g. "Ion Energy, Inc." → "ion-energy".
    """
    s = company_name.lower().strip()
    s = re.sub(r"[,\.]+$", "", s)                   # strip trailing punctuation
    s = re.sub(r"[^a-z0-9]+", "-", s)               # non-alphanumeric → hyphen
    s = re.sub(r"-+", "-", s).strip("-")             # collapse and strip hyphens
    return s


def _is_houston_location(location: str | None) -> bool:
    if not location:
        return False
    loc_lower = location.lower()
    return any(kw in loc_lower for kw in _HOUSTON_KEYWORDS)


def _is_remote_location(location: str | None) -> bool:
    if not location:
        return False
    return "remote" in location.lower()


def _empty_result() -> JobFeedsResult:
    return JobFeedsResult(
        found=False,
        platform=None,
        slug=None,
        total_jobs=0,
        houston_jobs=0,
        houston_job_titles=[],
        remote_jobs=0,
    )


# ── Greenhouse ─────────────────────────────────────────────────────────────────

def _try_greenhouse(slug: str) -> JobFeedsResult | None:
    """Attempt a Greenhouse jobs API lookup. Returns None on 404 or error."""
    url = _GREENHOUSE_URL.format(slug=slug)
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.debug(f"[job_feeds:greenhouse-error] {slug!r}: {exc}")
        return None

    try:
        data = resp.json()
    except ValueError:
        return None

    jobs = data.get("jobs", [])
    if not isinstance(jobs, list):
        return None

    return _aggregate_jobs(jobs, platform="greenhouse", slug=slug, loc_key="location.name")


def _get_greenhouse_location(job: dict) -> str | None:
    loc = job.get("location")
    if isinstance(loc, dict):
        return loc.get("name")
    if isinstance(loc, str):
        return loc
    return None


# ── Lever ──────────────────────────────────────────────────────────────────────

def _try_lever(slug: str) -> JobFeedsResult | None:
    """Attempt a Lever postings API lookup. Returns None on 404 or error."""
    url = _LEVER_URL.format(slug=slug)
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.debug(f"[job_feeds:lever-error] {slug!r}: {exc}")
        return None

    try:
        jobs = resp.json()
    except ValueError:
        return None

    if not isinstance(jobs, list):
        return None

    houston_titles: list[str] = []
    houston_count = 0
    remote_count = 0
    for job in jobs:
        categories = job.get("categories", {})
        location = categories.get("location") if isinstance(categories, dict) else None
        title = job.get("text", "")
        if _is_houston_location(location):
            houston_count += 1
            if len(houston_titles) < 10:
                houston_titles.append(title)
        if _is_remote_location(location):
            remote_count += 1

    logger.debug(
        f"[job_feeds:lever] {slug!r} → {len(jobs)} jobs, "
        f"{houston_count} houston, {remote_count} remote"
    )
    return JobFeedsResult(
        found=True,
        platform="lever",
        slug=slug,
        total_jobs=len(jobs),
        houston_jobs=houston_count,
        houston_job_titles=houston_titles,
        remote_jobs=remote_count,
    )


# ── Ashby ──────────────────────────────────────────────────────────────────────

def _try_ashby(slug: str) -> JobFeedsResult | None:
    """Attempt an Ashby job board API lookup. Returns None on 404 or error."""
    url = _ASHBY_URL.format(slug=slug)
    try:
        resp = requests.post(
            url,
            json={"limit": 100},
            headers={**_HEADERS, "Content-Type": "application/json"},
            timeout=_TIMEOUT,
        )
        if resp.status_code in (404, 422):
            return None
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.debug(f"[job_feeds:ashby-error] {slug!r}: {exc}")
        return None

    try:
        data = resp.json()
    except ValueError:
        return None

    jobs = data.get("results", [])
    if not isinstance(jobs, list):
        return None

    houston_titles: list[str] = []
    houston_count = 0
    remote_count = 0
    for job in jobs:
        location = job.get("location") or job.get("locationName")
        title = job.get("title", "")
        if _is_houston_location(location):
            houston_count += 1
            if len(houston_titles) < 10:
                houston_titles.append(title)
        if _is_remote_location(location):
            remote_count += 1

    logger.debug(
        f"[job_feeds:ashby] {slug!r} → {len(jobs)} jobs, "
        f"{houston_count} houston, {remote_count} remote"
    )
    return JobFeedsResult(
        found=True,
        platform="ashby",
        slug=slug,
        total_jobs=len(jobs),
        houston_jobs=houston_count,
        houston_job_titles=houston_titles,
        remote_jobs=remote_count,
    )


# ── Greenhouse aggregator helper ───────────────────────────────────────────────

def _aggregate_greenhouse_jobs(jobs: list[dict], slug: str) -> JobFeedsResult:
    houston_titles: list[str] = []
    houston_count = 0
    remote_count = 0
    for job in jobs:
        location = _get_greenhouse_location(job)
        title = job.get("title", "")
        if _is_houston_location(location):
            houston_count += 1
            if len(houston_titles) < 10:
                houston_titles.append(title)
        if _is_remote_location(location):
            remote_count += 1

    logger.debug(
        f"[job_feeds:greenhouse] {slug!r} → {len(jobs)} jobs, "
        f"{houston_count} houston, {remote_count} remote"
    )
    return JobFeedsResult(
        found=True,
        platform="greenhouse",
        slug=slug,
        total_jobs=len(jobs),
        houston_jobs=houston_count,
        houston_job_titles=houston_titles,
        remote_jobs=remote_count,
    )


def _aggregate_jobs(
    jobs: list[dict],
    platform: str,
    slug: str,
    loc_key: str,
) -> JobFeedsResult:
    """Build a JobFeedsResult from a flat list of job dicts (Greenhouse format)."""
    return _aggregate_greenhouse_jobs(jobs, slug)


# ── Public API ────────────────────────────────────────────────────────────────


def lookup_job_feeds(company_name: str) -> JobFeedsResult:
    """Look up open job postings for a company across Greenhouse, Lever, and Ashby.

    Tries each ATS in order using a slugified company name. Returns the first
    successful result. If all three fail (404 / network error), returns an
    empty result with found=False.

    Args:
        company_name: Company name as it appears in pipeline data.

    Returns:
        JobFeedsResult dict. `found` is True if any ATS returned valid data.
        `houston_jobs` counts roles with a Houston-area location string.
    """
    if not company_name or not company_name.strip():
        return _empty_result()

    slug = _slugify(company_name.strip())
    if not slug:
        return _empty_result()

    # Try Greenhouse first (largest ATS in tech/energy)
    result = _try_greenhouse(slug)
    if result is not None:
        return result

    # Try Lever
    result = _try_lever(slug)
    if result is not None:
        return result

    # Try Ashby
    result = _try_ashby(slug)
    if result is not None:
        return result

    logger.debug(f"[job_feeds:not-found] {company_name!r} (slug={slug!r}) not on GH/Lever/Ashby")
    return _empty_result()
