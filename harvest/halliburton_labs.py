"""
Halliburton Labs portfolio harvester.

Harvests company records from the Halliburton Labs companies page.
Halliburton Labs is a cohort-based accelerator (not a VC fund) that
provides energy startups with access to Halliburton's expertise,
facilities, equipment, and industry network.

Source attribution: "Halliburton Labs"
Page URL: https://halliburtonlabs.com/companies/

Design notes:
  - Page structure: static HTML, single-page listing. No detail pages.
    Each company is an <a class="grid-item participant"> element linking
    directly to the company's external website.
  - Two cohort types distinguished by CSS gradient class:
      warm-gradient → current participants (active cohort)
      cool-gradient → alumni (graduated cohort members)
    No cohort year data is present in the page markup; cohort_type
    ("current" or "alumni") is stored in extra for downstream scoring.
  - No pagination. One HTTP request fetches the entire company list.
  - Accelerator membership is a LOW-weight positive signal in the
    venture-scale rubric (Section 2). The downstream Houston presence
    scorer treats Halliburton Labs membership as a tier-B signal
    (confirmed Houston ecosystem connection even for non-HQ companies).
"""
from __future__ import annotations

import logging
from typing import ClassVar
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from harvest.base import BaseHarvester, RawCompanyRecord

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


class HalliburtonLabsHarvester(BaseHarvester):
    """Harvest company records from the Halliburton Labs companies page.

    Returns one RawCompanyRecord per company card. Cohort type (current vs.
    alumni) is stored in extra["cohort_type"]. All other data (name,
    description, website, location) is extracted from the card markup.
    """

    SOURCE_NAME: ClassVar[str] = "Halliburton Labs"
    SOURCE_URL: ClassVar[str] = "https://halliburtonlabs.com/companies/"
    SOURCE_TYPE: ClassVar[str] = "accelerator"
    UPDATE_CADENCE: ClassVar[str] = "monthly"
    SCRAPE_METHOD: ClassVar[str] = "static"
    AUTH_REQUIRED: ClassVar[bool] = False
    EXPECTED_YIELD: ClassVar[str] = "35-50"

    def fetch(self) -> list[RawCompanyRecord]:
        """Fetch the companies page and parse all company cards.

        Returns one RawCompanyRecord per card. Skips cards without a name.
        Empty list on HTTP error.
        """
        self.rate_limiter.wait()
        try:
            resp = requests.get(
                self.SOURCE_URL,
                headers=_HEADERS,
                timeout=20,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error(f"[halliburton:fetch-error] {exc}")
            raise

        soup = BeautifulSoup(resp.text, "lxml")
        cards = soup.select("a.grid-item.participant")

        if not cards:
            logger.warning(
                "[halliburton:no-cards] Zero company cards found — "
                "page structure may have changed (selector: a.grid-item.participant)"
            )
            return []

        logger.info(f"[halliburton:cards] {len(cards)} company cards found")

        records: list[RawCompanyRecord] = []
        for card in cards:
            name = self._extract_name(card)
            if not name:
                logger.debug("[halliburton:skip] Card missing name — skipped")
                continue

            website = self._extract_website(card)
            description = self._extract_description(card)
            location_raw = self._extract_location(card)
            cohort_type = self._extract_cohort_type(card)

            records.append(
                RawCompanyRecord(
                    name=name,
                    source=self.SOURCE_NAME,
                    source_url=self.SOURCE_URL,
                    description=description,
                    website=website,
                    location_raw=location_raw,
                    tags=[],
                    extra={"cohort_type": cohort_type},
                )
            )
            logger.debug(
                f"[halliburton:record] {name!r} "
                f"({cohort_type}, {location_raw}) → {website}"
            )

        logger.info(
            f"[halliburton:done] {len(records)} company records extracted"
        )
        return records

    # ── Card field extractors ─────────────────────────────────────────────────

    @staticmethod
    def _extract_name(card) -> str | None:
        """Extract company name from .grid-item-title."""
        el = card.select_one(".grid-item-title")
        if not el:
            return None
        name = el.get_text(strip=True)
        return name if name else None

    @staticmethod
    def _extract_website(card) -> str | None:
        """Extract company website from the card's href attribute."""
        href = card.get("href", "").strip()
        if not href or not href.startswith("http"):
            return None
        return href

    @staticmethod
    def _extract_description(card) -> str | None:
        """Extract description from .grid-item-description p."""
        el = card.select_one(".grid-item-description p")
        if not el:
            # Fallback: try the description div without p
            el = card.select_one(".grid-item-description")
        if not el:
            return None
        desc = el.get_text(strip=True)
        return desc if desc else None

    @staticmethod
    def _extract_location(card) -> str | None:
        """Extract location string from .grid-item-address."""
        el = card.select_one(".grid-item-address")
        if not el:
            return None
        loc = el.get_text(strip=True)
        return loc if loc else None

    @staticmethod
    def _extract_cohort_type(card) -> str:
        """Return 'current' for warm-gradient cards, 'alumni' for cool-gradient."""
        classes = card.get("class", [])
        if "warm-gradient" in classes:
            return "current"
        return "alumni"
