"""
Rice Business Plan Competition (RBPC) alumni harvester.

Harvests company records from the RBPC featured alumni page and per-year
competition participant tables at rbpc.rice.edu.

Source attribution: "RBPC Alumni"

Access pattern (per live-site inspection 2026-05-02):
  - Drupal 10 CMS; fully server-rendered static HTML. No JS required.
  - No Cloudflare or authentication. Plain HTTP GET with browser User-Agent works.
  - Note: source inventory specifies /alumni but that path returns 404.
    Correct URLs confirmed during inspection: /featured-alumni and /YEAR/startups.

Two page types harvested:

  1. /featured-alumni — ~18 notable alumni companies with exit/growth milestones.
     Structure: div.c--mosaic-card > div.text-container
       p > strong              → company name
       h4                      → competition year string (e.g. "2013 RBPC")
       p (second)              → placement (e.g. "Finalist", "Competitor")

  2. /YEAR/startups (years 2018–2026) — bare HTML table of all competition entrants.
     URL variant for current cycle: /YEAR/YEAR-startups (tried as fallback).
     Structure: table > tbody > tr
       td[0]  → startup name
       td[1]  → affiliated university
       td[2]  → optional <a href="..."> website link

Harvest strategy:
  - Fetch /featured-alumni first, then each year's startups table.
  - Deduplicate across all pages by normalized (lowercase, stripped) company name.
  - Emit one RawCompanyRecord per unique company.

Expected yield: 40-80 records (varies by years harvested and dedup overlap).
"""
from __future__ import annotations

import logging
import re
from typing import ClassVar

import requests
from bs4 import BeautifulSoup

from harvest.base import BaseHarvester, RawCompanyRecord

logger = logging.getLogger(__name__)

_BASE_URL = "https://rbpc.rice.edu"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

# Harvest years. Extend as new competitions occur.
_HARVEST_YEARS: list[int] = list(range(2018, 2027))


class RbpcAlumniHarvester(BaseHarvester):
    """Harvest company records from RBPC featured alumni and yearly startups tables.

    Pass 1: fetch /featured-alumni and parse mosaic cards (name, year, placement).
    Pass 2: for each year in _HARVEST_YEARS, fetch the startups table (name,
            university, website).

    Deduplicates across all pages by normalized name so that a company appearing
    on /featured-alumni and again in a yearly table is emitted only once (the
    featured-alumni record takes precedence, as it has richer metadata).

    Returns one RawCompanyRecord per unique company.
    """

    SOURCE_NAME: ClassVar[str] = "RBPC Alumni"
    SOURCE_URL: ClassVar[str] = f"{_BASE_URL}/featured-alumni"
    SOURCE_TYPE: ClassVar[str] = "event"
    UPDATE_CADENCE: ClassVar[str] = "annual"
    SCRAPE_METHOD: ClassVar[str] = "static"
    AUTH_REQUIRED: ClassVar[bool] = False
    EXPECTED_YIELD: ClassVar[str] = "100-200"

    def fetch(self) -> list[RawCompanyRecord]:
        """Fetch featured alumni, then per-year tables; deduplicate by name."""
        seen: set[str] = set()
        records: list[RawCompanyRecord] = []

        # Pass 1: featured alumni
        self.rate_limiter.wait()
        html = self._fetch_page(f"{_BASE_URL}/featured-alumni")
        if html:
            for rec in self._parse_featured_alumni(html):
                key = _normalize_name(rec.name)
                if key not in seen:
                    seen.add(key)
                    records.append(rec)
            logger.info(
                f"[rbpc:featured-alumni] {len(records)} unique alumni companies"
            )

        # Pass 2: per-year startups tables
        for year in _HARVEST_YEARS:
            self.rate_limiter.wait()
            html = self._fetch_year_startups(year)
            if html is None:
                logger.debug(f"[rbpc:{year}] no table found — skipping")
                continue

            year_records = self._parse_startups_table(html, year)
            new_count = 0
            for rec in year_records:
                key = _normalize_name(rec.name)
                if key not in seen:
                    seen.add(key)
                    records.append(rec)
                    new_count += 1

            logger.info(
                f"[rbpc:{year}] {len(year_records)} rows, {new_count} new unique"
            )

        logger.info(f"[rbpc:done] {len(records)} total unique records")
        return records

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def _fetch_page(self, url: str) -> str | None:
        """GET url; return HTML text or None on failure (4xx/5xx/network error)."""
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            logger.warning(f"[rbpc:fetch-error] {url}: {exc}")
            return None

    def _fetch_year_startups(self, year: int) -> str | None:
        """Try the canonical /YEAR/startups URL, then the /YEAR/YEAR-startups fallback.

        Returns HTML text on first successful fetch, or None if both 404.
        """
        for path in [f"/{year}/startups", f"/{year}/{year}-startups"]:
            html = self._fetch_page(f"{_BASE_URL}{path}")
            if html:
                return html
        return None

    # ── Parsers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_featured_alumni(html: str) -> list[RawCompanyRecord]:
        """Parse mosaic cards from the /featured-alumni page.

        Each div.c--mosaic-card card yields one RawCompanyRecord with name,
        competition year, and placement. Description is the success-story blurb
        with the leading company name stripped.
        """
        soup = BeautifulSoup(html, "lxml")
        records: list[RawCompanyRecord] = []

        for card in soup.select("div.c--mosaic-card"):
            text_container = card.select_one("div.text-container")
            if not text_container:
                continue

            name_el = text_container.select_one("p strong")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            if not name:
                continue

            year_el = text_container.select_one("h4")
            year_str = year_el.get_text(strip=True) if year_el else None
            competition_year = _parse_year(year_str)

            all_p = text_container.select("p")
            placement = all_p[1].get_text(strip=True) if len(all_p) > 1 else None

            # Strip leading company name from blurb text to produce a description
            description: str | None = None
            if all_p:
                blurb = all_p[0].get_text(strip=True)
                if blurb.startswith(name):
                    tail = blurb[len(name):].strip().lstrip("–—-").strip()
                    description = tail if tail else None
                else:
                    description = blurb or None

            records.append(
                RawCompanyRecord(
                    name=name,
                    source="RBPC Alumni",
                    source_url=f"{_BASE_URL}/featured-alumni",
                    description=description,
                    website=None,
                    location_raw=None,
                    tags=[],
                    extra={
                        "competition_year": competition_year,
                        "placement": placement,
                        "page": "featured-alumni",
                    },
                )
            )

        return records

    @staticmethod
    def _parse_startups_table(html: str, year: int) -> list[RawCompanyRecord]:
        """Parse the <table> on a /YEAR/startups page.

        Returns one RawCompanyRecord per <tbody> row with name, university
        (in extra), and optional website URL.
        """
        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table")
        if not table:
            return []

        records: list[RawCompanyRecord] = []
        for row in table.select("tbody tr"):
            cells = row.select("td")
            if not cells:
                continue
            name = cells[0].get_text(strip=True)
            if not name:
                continue

            university = cells[1].get_text(strip=True) if len(cells) > 1 else None

            website: str | None = None
            if len(cells) > 2:
                link = cells[2].find("a")
                if link and link.get("href"):
                    href = link["href"].strip()
                    if href.startswith("http"):
                        website = href

            records.append(
                RawCompanyRecord(
                    name=name,
                    source="RBPC Alumni",
                    source_url=f"{_BASE_URL}/{year}/startups",
                    description=None,
                    website=website,
                    location_raw=None,
                    tags=[],
                    extra={
                        "competition_year": year,
                        "university": university,
                        "page": f"{year}/startups",
                    },
                )
            )

        return records


# ── Helpers ───────────────────────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    """Lowercase, collapse whitespace — used for cross-page dedup keying."""
    return re.sub(r"\s+", " ", name.lower().strip())


def _parse_year(year_str: str | None) -> int | None:
    """Extract a 4-digit year from a string like '2013 RBPC'."""
    if not year_str:
        return None
    m = re.search(r"\b(20\d{2})\b", year_str)
    return int(m.group(1)) if m else None
