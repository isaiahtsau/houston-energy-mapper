"""
Greentown Houston member directory harvester.

Harvests company records from the Greentown Labs Houston member directory at
greentownlabs.com/members/?hq=houston.

Source attribution: "Greentown Houston"

Access pattern notes (per live-site inspection 2026-05-01):
  - The /members/?hq=houston page is WordPress with JS-loaded cards (AJAX).
    Rather than running Playwright, we POST directly to the WordPress AJAX
    endpoint used by the page's own JS bundle — no nonce or auth required:

        POST https://greentownlabs.com/wp-admin/admin-ajax.php
        Content-Type: application/x-www-form-urlencoded
        action=greentown_ajax_get_filter_members&hq=houston&page=N

    Response is a raw HTML fragment (not JSON) containing <a class="col-4 card">
    elements for each member. Paginate by incrementing page until the response
    contains a .no-results element or zero cards.

  - At build time: 245 Houston members across 10 pages (27/page except last).

Two-pass harvest:
  1. Paginate AJAX endpoint collecting slug, name, sector, short description,
     and detail-page URL for each card.
  2. Fetch each detail page (/members/{slug}/) to extract the full description
     and external company website URL.

Listing card structure (from AJAX fragment):
  a.col-4.card[href="/members/{slug}/"]
    h2.entry-title        → company name
    div.title1 > strong   → sector
    div.title2            → location label (contains "Houston")
    p.shortdesc           → short description (~10-20 words)

Detail page structure (static WordPress HTML):
  h1.entry-title          → company name (canonical; listing may truncate)
  .entry-content p        → full description paragraphs
  <a href="http...">      → first external link that is not greentownlabs.com
                            or a social/nav domain → company website

Expected yield: 200-280 records (245 confirmed at build time).
"""
from __future__ import annotations

import logging
import re
from typing import ClassVar
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

from harvest.base import BaseHarvester, RawCompanyRecord

logger = logging.getLogger(__name__)

_AJAX_URL = "https://greentownlabs.com/wp-admin/admin-ajax.php"
_BASE_URL = "https://greentownlabs.com"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}
_AJAX_HEADERS = {
    **_HEADERS,
    "Content-Type": "application/x-www-form-urlencoded",
    "X-Requested-With": "XMLHttpRequest",
}

# Domains to skip when looking for company website links on detail pages
_SKIP_LINK_HOSTS: frozenset[str] = frozenset({
    "greentownlabs.com",
    "www.greentownlabs.com",
    "linkedin.com",
    "www.linkedin.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "mailto:",
})


class GreentownHoustonHarvester(BaseHarvester):
    """Harvest member records from the Greentown Labs Houston directory.

    Two-pass harvest: paginated AJAX listing followed by individual detail
    page fetches to collect the full description and website URL.

    Returns one RawCompanyRecord per member. Sector tag is stored in
    extra["sector"]. The Greentown member slug is stored in extra["slug"].
    """

    SOURCE_NAME: ClassVar[str] = "Greentown Houston"
    SOURCE_URL: ClassVar[str] = "https://greentownlabs.com/members/?hq=houston"
    SOURCE_TYPE: ClassVar[str] = "accelerator"
    UPDATE_CADENCE: ClassVar[str] = "monthly"
    SCRAPE_METHOD: ClassVar[str] = "static"
    AUTH_REQUIRED: ClassVar[bool] = False
    EXPECTED_YIELD: ClassVar[str] = "200-280"

    def fetch(self) -> list[RawCompanyRecord]:
        """Paginate the AJAX listing, then fetch each detail page.

        Returns one RawCompanyRecord per member. Detail fetch failures are
        non-fatal: the record is emitted with listing-only data.
        """
        listing_items = self._fetch_all_listing_items()
        if not listing_items:
            return []

        logger.info(f"[greentown:listing] {len(listing_items)} members collected")

        records: list[RawCompanyRecord] = []
        for item in listing_items:
            self.rate_limiter.wait()
            detail = self._fetch_detail(item["detail_url"])
            website = detail.get("website") if detail else None
            description = (
                detail.get("description") or item.get("short_description")
            ) if detail else item.get("short_description")

            records.append(
                RawCompanyRecord(
                    name=item["name"],
                    source=self.SOURCE_NAME,
                    source_url=item["detail_url"],
                    description=description,
                    website=website,
                    location_raw="Houston, TX",
                    tags=[],
                    extra={
                        "sector": item.get("sector"),
                        "slug": item.get("slug"),
                    },
                )
            )
            logger.debug(
                f"[greentown:record] {item['name']!r} "
                f"({item.get('sector')}) → {website}"
            )

        logger.info(f"[greentown:done] {len(records)} records extracted")
        return records

    # ── Listing (AJAX pagination) ─────────────────────────────────────────────

    def _fetch_all_listing_items(self) -> list[dict]:
        """Paginate the AJAX endpoint and return all listing card dicts."""
        items: list[dict] = []
        page = 1

        while True:
            self.rate_limiter.wait()
            fragment = self._fetch_ajax_page(page)
            if fragment is None:
                break   # HTTP error — abort pagination

            page_items = self._parse_listing_fragment(fragment)
            if not page_items:
                logger.debug(f"[greentown:pagination] page {page} empty — done")
                break

            items.extend(page_items)
            logger.debug(
                f"[greentown:pagination] page {page}: {len(page_items)} cards "
                f"(total so far: {len(items)})"
            )
            page += 1

        return items

    def _fetch_ajax_page(self, page: int) -> str | None:
        """POST to the WordPress AJAX endpoint for one page of Houston members.

        Returns the raw HTML fragment string, or None on HTTP error.
        """
        payload = (
            f"action=greentown_ajax_get_filter_members"
            f"&hq=houston"
            f"&page={page}"
        )
        try:
            resp = requests.post(
                _AJAX_URL,
                data=payload,
                headers=_AJAX_HEADERS,
                timeout=20,
            )
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            logger.error(f"[greentown:ajax-error] page={page}: {exc}")
            raise

    @staticmethod
    def _parse_listing_fragment(html_fragment: str) -> list[dict]:
        """Parse card elements from a single AJAX response fragment.

        Returns empty list when the response contains a .no-results element
        or no matching card elements (signals pagination end).
        """
        soup = BeautifulSoup(html_fragment, "lxml")

        # No-results sentinel — WordPress renders this on empty pages
        if soup.select_one(".no-results"):
            return []

        items = []
        for card in soup.select("a.col-4.card"):
            name_el = card.select_one("h2.entry-title")
            sector_el = card.select_one("div.title1 strong")
            desc_el = card.select_one("p.shortdesc")
            detail_url = card.get("href", "").strip()

            name = name_el.get_text(strip=True) if name_el else None
            if not name:
                continue

            # Extract slug from detail URL
            slug_match = re.search(r"/members/([^/]+)/", detail_url)
            slug = slug_match.group(1) if slug_match else None

            items.append({
                "name": name,
                "sector": sector_el.get_text(strip=True) if sector_el else None,
                "short_description": desc_el.get_text(strip=True) if desc_el else None,
                "detail_url": detail_url,
                "slug": slug,
            })

        return items

    # ── Detail page ──────────────────────────────────────────────────────────

    def _fetch_detail(self, url: str) -> dict | None:
        """Fetch one /members/{slug}/ detail page and extract website + description.

        Returns a dict with keys "website" and "description", or None on failure.
        """
        if not url or not url.startswith("http"):
            return None
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning(f"[greentown:detail-error] {url}: {exc}")
            return None

        soup = BeautifulSoup(resp.text, "lxml")
        return {
            "website": self._extract_website(soup),
            "description": self._extract_description(soup),
        }

    @staticmethod
    def _extract_website(soup: BeautifulSoup) -> str | None:
        """Return the first external link on the detail page that is not a
        Greentown or social domain.

        The company website is typically the first outbound link in the
        main content area.
        """
        content = soup.select_one(".entry-content") or soup.select_one("main")
        if not content:
            return None
        for a in content.find_all("a", href=True):
            href = a["href"].strip()
            if not href.startswith("http"):
                continue
            host = urlparse(href).hostname or ""
            host = re.sub(r"^www\.", "", host)
            if host and host not in _SKIP_LINK_HOSTS and "greentownlabs" not in host:
                return href
        return None

    @staticmethod
    def _extract_description(soup: BeautifulSoup) -> str | None:
        """Extract the full description from the detail page .entry-content."""
        content = soup.select_one(".entry-content")
        if not content:
            return None
        for p in content.select("p"):
            text = p.get_text(strip=True)
            if len(text) > 40:
                return text
        return None
