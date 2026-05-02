"""
Energy Capital Ventures (ECV) portfolio harvester.

Harvests company records from the ECV portfolio page at
energycapitalventures.com/portfolio.

Source: https://energycapitalventures.com/portfolio
Source attribution: "Energy Capital Ventures"

Page structure notes (per live-site inspection 2026-05-01):
  - Portfolio index: static Webflow HTML, two w-dyn-items blocks
    (Fund I: 9 companies, Fund II: 3 companies).
    Each item is: div.w-dyn-item > a.portfolios-hero-link[href="/portfolio/{slug}"]
    Fund membership detected by nearest preceding h2/h3/h4 heading text.
  - Detail pages: static Webflow CMS template at /portfolio/{slug}.
    Company name:   h2.heading-b-36px
    Description:    first p.paragraph-16px in the content section
    Structured data: div.desc-card-wrap elements, each with:
        div.text-18px-bold (label, excluding .w-condition-invisible elements)
        div.paragraph-16px.projects (value)
    Known labels: "Headquarters", "Founders", "Investment"
    Founders are semicolon-delimited (e.g., "Alice Smith; Bob Jones, PhD").
  - No pagination. All 12 portfolio companies in two static blocks.
  - No external company website URL present in the static HTML —
    only name, description, location, founders, and investment date.

Expected yield: 10-15 records (12 confirmed at time of build).
"""
from __future__ import annotations

import logging
import re
import time
from typing import ClassVar
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from harvest.base import BaseHarvester, RawCompanyRecord

logger = logging.getLogger(__name__)

_BASE_URL = "https://energycapitalventures.com"
_PORTFOLIO_PATH = "/portfolio"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


class EnergyCapitalVenturesHarvester(BaseHarvester):
    """Harvest company records from the ECV portfolio.

    Two-pass harvest:
      1. Fetch the portfolio index to extract slug list + fund membership.
      2. Fetch each detail page to extract name, description, location,
         founders, and investment date.

    Returns one RawCompanyRecord per company. Fund membership (I or II)
    is stored in extra["fund"].
    """

    SOURCE_NAME: ClassVar[str] = "Energy Capital Ventures"
    SOURCE_URL: ClassVar[str] = "https://energycapitalventures.com/portfolio"
    SOURCE_TYPE: ClassVar[str] = "vc_portfolio"
    UPDATE_CADENCE: ClassVar[str] = "quarterly"
    SCRAPE_METHOD: ClassVar[str] = "static"
    AUTH_REQUIRED: ClassVar[bool] = False
    EXPECTED_YIELD: ClassVar[str] = "10-15"

    def fetch(self) -> list[RawCompanyRecord]:
        """Fetch portfolio index then walk each detail page.

        Returns one RawCompanyRecord per company. Skips companies where
        the detail page fails or returns no name. Empty list on index fetch error.
        """
        slugs_with_fund = self._fetch_index()
        if not slugs_with_fund:
            return []

        logger.info(f"[ecv:index] {len(slugs_with_fund)} portfolio slugs found")

        records: list[RawCompanyRecord] = []
        for slug, fund in slugs_with_fund:
            self.rate_limiter.wait()
            record = self._fetch_detail(slug, fund)
            if record is not None:
                records.append(record)
                logger.debug(
                    f"[ecv:record] {record.name!r} "
                    f"(Fund {fund}, {record.location_raw}) — {slug}"
                )

        logger.info(f"[ecv:done] {len(records)} records extracted")
        return records

    # ── Index page ────────────────────────────────────────────────────────────

    def _fetch_index(self) -> list[tuple[str, str]]:
        """Fetch the portfolio index and return (slug, fund) pairs.

        Fund is "I" or "II" based on the nearest heading element above
        each w-dyn-items block. Falls back to "unknown" if heading not found.
        """
        url = urljoin(_BASE_URL, _PORTFOLIO_PATH)
        self.rate_limiter.wait()
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error(f"[ecv:index-fetch-error] {exc}")
            raise

        soup = BeautifulSoup(resp.text, "lxml")
        results: list[tuple[str, str]] = []

        for items_block in soup.select("div.w-dyn-items"):
            fund = self._detect_fund(items_block)
            for a_tag in items_block.select("a.portfolios-hero-link"):
                href = a_tag.get("href", "")
                # href is a relative path like /portfolio/graphitic
                slug_match = re.match(r"^/portfolio/([^/?#]+)", href)
                if slug_match:
                    results.append((slug_match.group(1), fund))

        if not results:
            logger.warning(
                "[ecv:index-no-slugs] No portfolio slugs found — "
                "page structure may have changed (selector: a.portfolios-hero-link)"
            )

        return results

    @staticmethod
    def _detect_fund(items_block: Tag) -> str:
        """Walk backwards through the DOM to find a heading naming the fund.

        Returns "I", "II", or "unknown".
        """
        # Walk up to find the parent container, then look for a preceding heading
        parent = items_block.parent
        if parent is None:
            return "unknown"
        for element in parent.find_all_previous(["h1", "h2", "h3", "h4"], limit=5):
            text = element.get_text(strip=True)
            if re.search(r"\bFund\s+II\b", text, re.I):
                return "II"
            if re.search(r"\bFund\s+I\b", text, re.I):
                return "I"
        return "unknown"

    # ── Detail page ──────────────────────────────────────────────────────────

    def _fetch_detail(self, slug: str, fund: str) -> RawCompanyRecord | None:
        """Fetch one portfolio detail page and return a RawCompanyRecord.

        Returns None if the page cannot be fetched or yields no company name.
        """
        url = urljoin(_BASE_URL, f"/portfolio/{slug}")
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning(f"[ecv:detail-fetch-error] {slug!r}: {exc}")
            return None

        soup = BeautifulSoup(resp.text, "lxml")

        name = self._extract_name(soup)
        if not name:
            logger.warning(f"[ecv:detail-no-name] {slug!r}: h2.heading-b-36px not found")
            return None

        description = self._extract_description(soup)
        fields = self._extract_desc_card_fields(soup)

        location_raw = fields.get("headquarters")
        founders_raw = fields.get("founders")
        investment_date = fields.get("investment")

        founders: list[str] = (
            [f.strip() for f in founders_raw.split(";") if f.strip()]
            if founders_raw
            else []
        )

        return RawCompanyRecord(
            name=name,
            source=self.SOURCE_NAME,
            source_url=url,
            description=description,
            website=None,   # external website not present in ECV static HTML
            location_raw=location_raw,
            tags=[],
            extra={
                "fund": fund,
                "founders": founders,
                "investment_date": investment_date,
                "slug": slug,
            },
        )

    # ── Field extractors ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_name(soup: BeautifulSoup) -> str | None:
        """Extract company name from h2.heading-b-36px."""
        el = soup.select_one("h2.heading-b-36px")
        if not el:
            return None
        name = el.get_text(strip=True)
        return name if name else None

    @staticmethod
    def _extract_description(soup: BeautifulSoup) -> str | None:
        """Extract the first paragraph description from p.paragraph-16px.

        The detail page uses p.paragraph-16px for the free-text description
        and div.paragraph-16px.projects for the structured card values.
        We want the <p> tag, not the divs.
        """
        el = soup.select_one("p.paragraph-16px")
        if not el:
            return None
        desc = el.get_text(strip=True)
        return desc if desc else None

    @staticmethod
    def _extract_desc_card_fields(soup: BeautifulSoup) -> dict[str, str]:
        """Extract label→value pairs from div.desc-card-wrap elements.

        Each card has:
          div.text-18px-bold          → label (skip .w-condition-invisible)
          div.paragraph-16px.projects → value

        Returns dict with lowercase, stripped label keys.
        Known keys: "headquarters", "founders", "investment".
        """
        fields: dict[str, str] = {}
        for card in soup.select("div.desc-card-wrap"):
            # Label: first bold div that is NOT the conditional visibility fallback
            label_els = [
                el for el in card.select("div.text-18px-bold")
                if "w-condition-invisible" not in (el.get("class") or [])
            ]
            value_el = card.select_one("div.paragraph-16px.projects")
            if not label_els or not value_el:
                continue
            label = label_els[0].get_text(strip=True).lower()
            value = value_el.get_text(strip=True)
            if label and value:
                fields[label] = value
        return fields
