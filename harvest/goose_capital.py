"""
GOOSE Capital portfolio harvester.

Harvests company records from the GOOSE Capital portfolio page at
www.goose.capital/portfolio.

Source: https://www.goose.capital/portfolio
Source attribution: "GOOSE Capital"

Domain note: the correct domain is goose.capital (NOT goosecapital.com —
that domain does not resolve). Pre-implementation inspection confirmed
www.goose.capital/portfolio returns HTTP 200 with 30 company items.

Page structure notes (per live-site inspection 2026-05-01):
  - Single-page Webflow CMS portfolio — no detail pages.
  - 30 company items at time of build (mixed sectors; classifier filters
    to energy/industrial subset during the classify stage).
  - Each item:
      div.companies__item.w-dyn-item
        └── a.companies__card.w-inline-block  (href = external company website)
              ├── img.companies__logo          (alt=""; name inferred from src filename)
              └── div.companies__screen        (opacity:0 in markup but present in DOM)
                    └── div.companies__description  (description text)
  - Company name is NOT present as a text node. It is inferred from the
    Webflow CDN image src filename by stripping the asset hash prefix and
    common logo-suffix words. This produces an approximate name adequate
    for cross-source dedup (Step 10). Examples:
        "{hash}_adhesys_logo.png"    → "Adhesys"
        "{hash}_zibrio_logo.png"     → "Zibrio"
        "{hash}_calyx-global_logo"   → "Calyx Global"
  - External website URL is extracted directly from a.companies__card href.
    Some hrefs include URL-encoded query parameters (e.g., strongroom's
    redirect); the raw href is stored as-is.

Expected yield: 20-35 records (30 confirmed at time of build).
"""
from __future__ import annotations

import logging
import re
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

# Webflow CDN asset hash: 24 lowercase hex chars followed by underscore
_WEBFLOW_HASH_RE = re.compile(r"^[0-9a-f]{24}_", re.I)

# Image suffix words to strip before title-casing the company name.
# Order matters: longer patterns first to avoid partial matches.
_LOGO_SUFFIX_RE = re.compile(
    r"[-_]?(logo|icon|img|image|thumbnail|photo|full|white|dark|color|bw|black|new|v\d+).*$",
    re.I,
)


def _name_from_image_src(src: str) -> str | None:
    """Infer a company display name from a Webflow CDN image URL.

    Webflow CDN filenames are prefixed with a 24-character asset hash:
        "{24-hex-chars}_{actual_filename}.png"
    After stripping the prefix, the filename is cleaned up to yield a
    best-guess display name.

    Returns None if the src is empty or parsing yields an empty string.
    """
    if not src:
        return None
    # Extract filename from CDN path
    path = urlparse(src).path
    filename = path.rsplit("/", 1)[-1]
    # Strip Webflow hash prefix
    filename = _WEBFLOW_HASH_RE.sub("", filename)
    # Remove file extension
    name = re.sub(r"\.(png|jpg|jpeg|webp|svg|gif)$", "", filename, flags=re.I)
    # Remove logo/icon suffix words and everything after
    name = _LOGO_SUFFIX_RE.sub("", name)
    # Normalise separators → spaces, title-case
    name = name.replace("-", " ").replace("_", " ").strip().title()
    return name if name else None


class GooseCapitalHarvester(BaseHarvester):
    """Harvest company records from the GOOSE Capital portfolio page.

    Single-page harvest: one HTTP request fetches all company items.
    Returns one RawCompanyRecord per company card. Name is inferred from
    the logo image filename; description and website URL come from the card.
    """

    SOURCE_NAME: ClassVar[str] = "GOOSE Capital"
    SOURCE_URL: ClassVar[str] = "https://www.goose.capital/portfolio"
    SOURCE_TYPE: ClassVar[str] = "vc_portfolio"
    UPDATE_CADENCE: ClassVar[str] = "quarterly"
    SCRAPE_METHOD: ClassVar[str] = "static"
    AUTH_REQUIRED: ClassVar[bool] = False
    EXPECTED_YIELD: ClassVar[str] = "20-35"

    def fetch(self) -> list[RawCompanyRecord]:
        """Fetch the portfolio page and parse all company cards.

        Returns one RawCompanyRecord per card. Skips cards where name
        inference and website extraction both fail (nothing useful to store).
        Empty list on HTTP error.
        """
        self.rate_limiter.wait()
        try:
            resp = requests.get(self.SOURCE_URL, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error(f"[goose:fetch-error] {exc}")
            raise

        soup = BeautifulSoup(resp.text, "lxml")
        items = soup.select("div.companies__item.w-dyn-item")

        if not items:
            logger.warning(
                "[goose:no-items] Zero company items found — "
                "page structure may have changed "
                "(selector: div.companies__item.w-dyn-item)"
            )
            return []

        logger.info(f"[goose:items] {len(items)} company items found")

        records: list[RawCompanyRecord] = []
        for item in items:
            a_tag = item.select_one("a.companies__card")
            img_tag = item.select_one("img.companies__logo")
            desc_el = item.select_one("div.companies__description")

            website = self._extract_website(a_tag)
            name = self._extract_name(img_tag)
            description = self._extract_description(desc_el)

            if not name and not website:
                logger.debug("[goose:skip] Card missing both name and website — skipped")
                continue

            # Fall back to domain-derived name when image parse fails
            if not name and website:
                name = self._name_from_domain(website)

            if not name:
                logger.debug(f"[goose:skip] Could not derive name (website={website!r}) — skipped")
                continue

            records.append(
                RawCompanyRecord(
                    name=name,
                    source=self.SOURCE_NAME,
                    source_url=self.SOURCE_URL,
                    description=description,
                    website=website,
                    location_raw=None,   # not available in static HTML
                    tags=[],
                    extra={},
                )
            )
            logger.debug(f"[goose:record] {name!r} → {website}")

        logger.info(f"[goose:done] {len(records)} company records extracted")
        return records

    # ── Card field extractors ─────────────────────────────────────────────────

    @staticmethod
    def _extract_website(a_tag) -> str | None:
        """Extract company website from the card anchor href."""
        if a_tag is None:
            return None
        href = a_tag.get("href", "").strip()
        if not href or not href.startswith("http"):
            return None
        return href

    @staticmethod
    def _extract_name(img_tag) -> str | None:
        """Infer company name from the logo image src filename."""
        if img_tag is None:
            return None
        src = img_tag.get("src", "")
        return _name_from_image_src(src)

    @staticmethod
    def _extract_description(desc_el) -> str | None:
        """Extract description text from div.companies__description."""
        if desc_el is None:
            return None
        desc = desc_el.get_text(strip=True)
        return desc if desc else None

    @staticmethod
    def _name_from_domain(url: str) -> str | None:
        """Derive a fallback company name from the website domain.

        Used when image-based name extraction fails.
        E.g. "https://www.adhesys-medical.com" → "Adhesys Medical"
        """
        try:
            host = urlparse(url).hostname or ""
            # Remove www. and TLD
            host = re.sub(r"^www\.", "", host)
            host = re.sub(r"\.[a-z]{2,}$", "", host)
            name = host.replace("-", " ").replace("_", " ").strip().title()
            return name if name else None
        except Exception:
            return None
