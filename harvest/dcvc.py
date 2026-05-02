"""
DCVC portfolio harvester.

Harvests company records from the DCVC portfolio page at dcvc.com/companies.

Source attribution: "DCVC"

Access pattern (per live-site inspection 2026-05-02):
  - Server-rendered HTML with Petite Vue for client-side filtering. All 289 company
    cards are present in the initial HTTP response — no JavaScript execution required.
  - No Cloudflare, no Memberstack, no authentication.
  - Correct URL: https://www.dcvc.com/companies (dcvc.com/companies → 404).

Page structure:
  article.company-card[data-sector="all,{s1},{s2}"][data-status="all,{status}"][data-portfolio="all,{fund}"]
    a.company-card__figure-link[aria-label="{company name}"][href="https://www.dcvc.com/companies/{slug}"]
      div.company-card-wrapper
        div.company-card__text-wrapper
          div.company-card__top
            h3.company-card__headline > span.highlight__target  ← company name (also in aria-label)
            p.company-card__desc                                ← company description
            div.company-card__additional-text                   ← exit info (exits only)

Data extracted per card:
  - Company name: aria-label on the <a> tag (reliable, always present)
  - Description: p.company-card__desc text
  - Sectors: data-sector attribute (comma-separated; "all" token stripped)
             converted to human-readable tags (e.g. "climate-tech" → "Climate Tech")
  - Status: data-status attribute ("current" | "exits")
  - Fund: data-portfolio attribute ("dcvc" | "dcvcBio" | "featured" combinations)
  - Source URL: the company's DCVC detail page URL (href)

Both current portfolio companies and exits are harvested — exits are still
venture-scale signal candidates. The classifier and dedup stage handle filtering.

Expected yield: 150-300 records (~289 cards at time of build).
"""
from __future__ import annotations

import logging
import re
from typing import ClassVar

import requests
from bs4 import BeautifulSoup

from harvest.base import BaseHarvester, RawCompanyRecord

logger = logging.getLogger(__name__)

_SOURCE_URL = "https://www.dcvc.com/companies"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


class DcvcHarvester(BaseHarvester):
    """Harvest company records from the DCVC portfolio page.

    Single-page harvest: one HTTP GET returns all 289 company cards. Name,
    description, sectors, fund, and status are all available in the static HTML.

    Returns one RawCompanyRecord per company. Sectors from data-sector are
    stored as tags (human-readable form). Status and fund are stored in extra.
    """

    SOURCE_NAME: ClassVar[str] = "DCVC"
    SOURCE_URL: ClassVar[str] = _SOURCE_URL
    SOURCE_TYPE: ClassVar[str] = "vc_portfolio"
    UPDATE_CADENCE: ClassVar[str] = "quarterly"
    SCRAPE_METHOD: ClassVar[str] = "static"
    AUTH_REQUIRED: ClassVar[bool] = False
    EXPECTED_YIELD: ClassVar[str] = "150-300"

    def fetch(self) -> list[RawCompanyRecord]:
        """Fetch the portfolio page and parse all company cards.

        Returns one RawCompanyRecord per company. Empty list on HTTP error.
        """
        self.rate_limiter.wait()
        try:
            resp = requests.get(_SOURCE_URL, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error(f"[dcvc:fetch-error] {exc}")
            raise

        soup = BeautifulSoup(resp.text, "lxml")
        records = self._parse_cards(soup)
        logger.info(f"[dcvc:done] {len(records)} records extracted")
        return records

    @staticmethod
    def _parse_cards(soup: BeautifulSoup) -> list[RawCompanyRecord]:
        """Parse all article.company-card elements into RawCompanyRecord instances."""
        cards = soup.select("article.company-card")
        if not cards:
            logger.warning(
                "[dcvc:no-cards] Zero company cards found — "
                "page structure may have changed (selector: article.company-card)"
            )
            return []

        logger.info(f"[dcvc:cards] {len(cards)} cards found")
        records: list[RawCompanyRecord] = []

        for card in cards:
            link = card.select_one("a.company-card__figure-link")
            if not link:
                continue

            name = link.get("aria-label", "").strip()
            href = link.get("href", "").strip()

            if not name:
                # Fallback: text from h3 span
                h3_span = card.select_one("h3.company-card__headline span.highlight__target")
                name = h3_span.get_text(strip=True) if h3_span else ""

            if not name:
                logger.debug(f"[dcvc:skip] card href={href!r} has no name — skipped")
                continue

            desc_el = card.select_one("p.company-card__desc")
            description = desc_el.get_text(strip=True) if desc_el else None

            tags = _parse_sectors(card.get("data-sector", ""))
            status = _strip_all_token(card.get("data-status", ""))
            fund_tokens = _strip_all_token(card.get("data-portfolio", ""))

            slug_m = re.search(r"/companies/([^/]+)$", href)
            slug = slug_m.group(1) if slug_m else None

            records.append(
                RawCompanyRecord(
                    name=name,
                    source="DCVC",
                    source_url=href if href.startswith("http") else None,
                    description=description,
                    website=None,      # not available on listing page
                    location_raw=None,
                    tags=tags,
                    extra={
                        "status": status or None,
                        "fund": fund_tokens or None,
                        "slug": slug,
                    },
                )
            )
            logger.debug(f"[dcvc:record] {name!r} sectors={tags} status={status!r}")

        return records


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_all_token(raw: str) -> str:
    """Strip the 'all,' prefix from a data-* attribute value.

    "all,climate-tech,industrial-transformation" → "climate-tech,industrial-transformation"
    "all,current"                                → "current"
    "all,"                                       → ""
    """
    return re.sub(r"^all,?", "", raw.strip())


def _parse_sectors(raw_sector: str) -> list[str]:
    """Convert data-sector attribute value to a list of human-readable sector tags.

    "all,climate-tech,industrial-transformation"
        → ["Climate Tech", "Industrial Transformation"]
    "all,computational-bio-and-chem"
        → ["Computational Bio And Chem"]  (title-cased slug; classifier handles it)
    """
    stripped = _strip_all_token(raw_sector)
    if not stripped:
        return []
    sectors = []
    for s in stripped.split(","):
        s = s.strip()
        if s:
            # Convert slug to human-readable: hyphens → spaces, title-case
            sectors.append(s.replace("-", " ").title())
    return sectors
