"""
Lowercarbon Capital portfolio harvester.

Harvests company records from the Lowercarbon Capital portfolio page at
lowercarbon.com/companies.

Source attribution: "Lowercarbon Capital"

Access pattern (per live-site inspection 2026-05-02):
  - Static WordPress HTML — all 101 company cards are server-rendered in the
    initial HTTP response. No JavaScript execution required.
  - No Cloudflare, no Memberstack, no authentication.
  - Canonical URL: https://lowercarbon.com/companies
    (lowercarboncapital.com/companies → 301 → lowercarbon.com/companies)

Page structure:
  div.company-cards                          ← grid container
    a.company-card[href="https://lowercarbon.com/company/{slug}/"]  ← card root (the <a> IS the card)
      div.company-card__content
        div.company-card__content-text
          h4.title-lg-company                ← tagline / one-line description
          h5.text-base                       ← company name

Single-page harvest — no detail page fetches. Each card provides:
  - Company name (explicit text node in h5)
  - Tagline/description (h4)
  - Source URL (card href → internal Lowercarbon detail page; no external website)

Deduplication: slug-keyed in-page dedup in case of duplicate card elements.

Expected yield: 90-110 records (~101 cards at time of build).
"""
from __future__ import annotations

import logging
import re
from typing import ClassVar

import requests
from bs4 import BeautifulSoup

from harvest.base import BaseHarvester, RawCompanyRecord

logger = logging.getLogger(__name__)

_SOURCE_URL = "https://lowercarbon.com/companies"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


class LowercarbonHarvester(BaseHarvester):
    """Harvest company records from the Lowercarbon Capital portfolio page.

    Single-page harvest: one HTTP GET returns all company cards. Name and tagline
    are explicit text nodes in each card. No detail page fetches needed.

    Returns one RawCompanyRecord per unique company. Tagline stored as description.
    Internal Lowercarbon detail URL stored as source_url (no external website on
    listing page). Deduplicates by slug within the page.
    """

    SOURCE_NAME: ClassVar[str] = "Lowercarbon Capital"
    SOURCE_URL: ClassVar[str] = _SOURCE_URL
    SOURCE_TYPE: ClassVar[str] = "vc_portfolio"
    UPDATE_CADENCE: ClassVar[str] = "quarterly"
    SCRAPE_METHOD: ClassVar[str] = "static"
    AUTH_REQUIRED: ClassVar[bool] = False
    EXPECTED_YIELD: ClassVar[str] = "90-110"

    def fetch(self) -> list[RawCompanyRecord]:
        """Fetch the portfolio page and parse all company cards.

        Returns one RawCompanyRecord per unique company. Empty list on HTTP error.
        """
        self.rate_limiter.wait()
        try:
            resp = requests.get(_SOURCE_URL, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error(f"[lowercarbon:fetch-error] {exc}")
            raise

        soup = BeautifulSoup(resp.text, "lxml")
        records = self._parse_cards(soup)
        logger.info(f"[lowercarbon:done] {len(records)} records extracted")
        return records

    @staticmethod
    def _parse_cards(soup: BeautifulSoup) -> list[RawCompanyRecord]:
        """Parse all a.company-card elements into RawCompanyRecord instances.

        Deduplicates by slug (company-card href path). Returns one record per
        unique slug.
        """
        cards = soup.select("a.company-card")
        if not cards:
            logger.warning(
                "[lowercarbon:no-cards] Zero company cards found — "
                "page structure may have changed (selector: a.company-card)"
            )
            return []

        logger.info(f"[lowercarbon:cards] {len(cards)} cards found")

        seen_slugs: set[str] = set()
        records: list[RawCompanyRecord] = []

        for card in cards:
            href = card.get("href", "").strip()
            slug = _slug_from_href(href)

            if slug and slug in seen_slugs:
                logger.debug(f"[lowercarbon:dedup] skipping duplicate slug={slug!r}")
                continue
            if slug:
                seen_slugs.add(slug)

            name_el = card.select_one("h5.text-base")
            desc_el = card.select_one("h4.title-lg-company")

            name = name_el.get_text(strip=True) if name_el else None
            description = desc_el.get_text(strip=True) if desc_el else None

            if not name:
                logger.debug(f"[lowercarbon:skip] card href={href!r} has no name — skipped")
                continue

            records.append(
                RawCompanyRecord(
                    name=name,
                    source="Lowercarbon Capital",
                    source_url=href if href.startswith("http") else None,
                    description=description,
                    website=None,          # not available on listing page
                    location_raw=None,     # not available on listing page
                    tags=[],
                    extra={"slug": slug},
                )
            )
            desc_preview = (description or "")[:50]
            logger.debug(f"[lowercarbon:record] {name!r} ({desc_preview!r})")

        return records


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slug_from_href(href: str) -> str | None:
    """Extract the slug from a Lowercarbon company detail URL.

    "https://lowercarbon.com/company/antora/" → "antora"
    """
    m = re.search(r"/company/([^/]+)/?$", href)
    return m.group(1) if m else None
