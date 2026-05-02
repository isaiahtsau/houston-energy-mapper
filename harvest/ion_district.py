"""
Ion District tenant directory harvester.

Harvests company records from the Ion District tenant directory at
iondistrict.com/visit/ion/.

Source attribution: "Ion District"

Access pattern (per live-site inspection 2026-05-02):
  - WordPress site with Cloudflare CDN. The Offices tenant list is server-rendered
    in the initial HTTP response; no JavaScript execution required.
  - Cloudflare bot-mitigation script is present but does not block plain HTTP GET
    requests with a standard browser User-Agent at build time.
  - Structure: div#ion-directory contains three <h3>/<ul> pairs:
        "Building Resources" | "Food & Drink" | "Offices"
    Only the "Offices" section is harvested; the other two are amenities.
  - Each office tenant:
        <li class="tenant l{N}">
          <a href="/tenants/{slug}/">
            <span>Name</span><sup>Level</sup>
          </a>
        </li>

Two-pass harvest:
  1. Fetch /visit/ion/, parse the Offices <ul> to collect slug, display name, floor.
  2. Fetch each /tenants/{slug}/ detail page for canonical name, description, website.

Detail page structure (WordPress custom post type "tenants"):
  h1.tenant-title span    → canonical company name (may include " - Nexus" suffix)
  p.description           → company description
  a.primary-button[href]  → company website

Name normalization:
  Some tenants include program suffixes: "Aikynetix – Nexus", "Ampla – Nexus (Coming Soon)".
  The program suffix (after " – " or " - ") is stored in extra["program"].
  "(Coming Soon)" is stripped; extra["coming_soon"]=True is set.
  The clean company name (before the dash) is stored in name.

Expected yield: 40-60 records (~40-60 office tenants at build time).
"""
from __future__ import annotations

import logging
import re
from typing import ClassVar
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, Tag

from harvest.base import BaseHarvester, RawCompanyRecord

logger = logging.getLogger(__name__)

_BASE_URL = "https://iondistrict.com"
_LISTING_URL = f"{_BASE_URL}/visit/ion/"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

_SKIP_LINK_HOSTS: frozenset[str] = frozenset({
    "iondistrict.com",
    "www.iondistrict.com",
    "linkedin.com",
    "www.linkedin.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
})


class IonDistrictHarvester(BaseHarvester):
    """Harvest tenant records from the Ion District offices directory.

    Two-pass harvest: listing page (Offices section only) followed by
    individual detail page fetches for canonical name, description, and website.

    Returns one RawCompanyRecord per office tenant. Program suffix (e.g. "Nexus")
    is stored in extra["program"]. Floor level stored in extra["floor"].
    Coming-soon tenants are included with extra["coming_soon"]=True.
    """

    SOURCE_NAME: ClassVar[str] = "Ion District"
    SOURCE_URL: ClassVar[str] = _LISTING_URL
    SOURCE_TYPE: ClassVar[str] = "accelerator_innovation_district"
    UPDATE_CADENCE: ClassVar[str] = "quarterly"
    SCRAPE_METHOD: ClassVar[str] = "static"
    AUTH_REQUIRED: ClassVar[bool] = False
    EXPECTED_YIELD: ClassVar[str] = "40-60"

    def fetch(self) -> list[RawCompanyRecord]:
        """Fetch the Offices listing, then detail pages for each tenant."""
        self.rate_limiter.wait()
        html = self._fetch_listing()
        if not html:
            return []

        listing_items = self._parse_office_listing(html)
        logger.info(f"[ion:listing] {len(listing_items)} office tenants found")

        records: list[RawCompanyRecord] = []
        for item in listing_items:
            self.rate_limiter.wait()
            detail = self._fetch_detail(item["detail_url"])

            name = item["display_name"]
            program = item.get("program")
            coming_soon = item.get("coming_soon", False)
            description = None
            website = None

            if detail:
                if detail.get("name"):
                    raw_name, detail_program = _split_program_suffix(detail["name"])
                    name = raw_name
                    if detail_program:
                        program = detail_program
                description = detail.get("description")
                website = detail.get("website")

            records.append(
                RawCompanyRecord(
                    name=name,
                    source=self.SOURCE_NAME,
                    source_url=item["detail_url"],
                    description=description,
                    website=website,
                    location_raw="Houston, TX",
                    tags=[],
                    extra={
                        "floor": item.get("floor"),
                        "program": program,
                        "coming_soon": coming_soon,
                        "slug": item.get("slug"),
                    },
                )
            )
            logger.debug(
                f"[ion:record] {name!r} floor={item.get('floor')} → {website}"
            )

        logger.info(f"[ion:done] {len(records)} records extracted")
        return records

    # ── Listing ───────────────────────────────────────────────────────────────

    def _fetch_listing(self) -> str | None:
        try:
            resp = requests.get(_LISTING_URL, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            logger.error(f"[ion:listing-error] {exc}")
            raise

    @staticmethod
    def _parse_office_listing(html: str) -> list[dict]:
        """Parse the Offices section from the /visit/ion/ listing page.

        Page structure (as of 2026-05-02):
          div#ion-directory
            div.section-map-places
              div                    ← Building Resources section
                h3 "Building Resources"
                ul.tenants-ul
                  li.tenant ...
              div                    ← Food & Drink section
              div                    ← Offices section
                h3 "Offices"
                ul.tenants-ul
                  li.tenant ...

        Only items from the section whose h3 contains "Offices" are returned.

        Returns list of dicts: slug, display_name, floor, detail_url, program,
        coming_soon.
        """
        soup = BeautifulSoup(html, "lxml")

        # The three sections (Building Resources, Food & Drink, Offices) are
        # sibling divs inside div.section-map-places. Each contains an h3 heading
        # and a ul.tenants-ul with li.tenant items.
        map_places = soup.select_one("div.section-map-places")
        if map_places is None:
            # Fallback: search from #ion-directory or whole page
            map_places = soup.select_one("#ion-directory") or soup

        items: list[dict] = []

        for section_div in map_places.children:
            if not isinstance(section_div, Tag):
                continue
            h3 = section_div.find("h3")
            if not h3:
                continue
            if "office" not in h3.get_text(strip=True).lower():
                continue

            # This is the Offices section div
            for li in section_div.select("li.tenant"):
                a = li.find("a", href=True)
                if not a:
                    continue
                href = a.get("href", "").strip()
                span = a.find("span")
                sup = a.find("sup")

                display_name_raw = (
                    span.get_text(strip=True) if span else a.get_text(strip=True)
                )
                floor = sup.get_text(strip=True) if sup else None

                slug_m = re.search(r"/tenants/([^/]+)/?$", href)
                slug = slug_m.group(1) if slug_m else None

                detail_url = (
                    href if href.startswith("http") else f"{_BASE_URL}{href}"
                )
                clean_name, program, coming_soon = _parse_display_name(display_name_raw)

                items.append({
                    "slug": slug,
                    "display_name": clean_name,
                    "floor": floor,
                    "detail_url": detail_url,
                    "program": program,
                    "coming_soon": coming_soon,
                })

            break  # Stop after the Offices section div

        return items

    # ── Detail page ───────────────────────────────────────────────────────────

    def _fetch_detail(self, url: str) -> dict | None:
        if not url or not url.startswith("http"):
            return None
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning(f"[ion:detail-error] {url}: {exc}")
            return None

        soup = BeautifulSoup(resp.text, "lxml")
        return {
            "name": self._extract_name(soup),
            "description": self._extract_description(soup),
            "website": self._extract_website(soup),
        }

    @staticmethod
    def _extract_name(soup: BeautifulSoup) -> str | None:
        el = soup.select_one("h1.tenant-title span")
        return el.get_text(strip=True) if el else None

    @staticmethod
    def _extract_description(soup: BeautifulSoup) -> str | None:
        el = soup.select_one("p.description")
        if el:
            text = el.get_text(separator=" ", strip=True)
            return text if len(text) > 20 else None
        return None

    @staticmethod
    def _extract_website(soup: BeautifulSoup) -> str | None:
        """Return the primary-button href if it points to an external company site."""
        btn = soup.select_one("a.primary-button")
        if btn and btn.get("href"):
            href = btn["href"].strip()
            if href.startswith("http"):
                host = re.sub(r"^www\.", "", urlparse(href).hostname or "")
                if host and host not in _SKIP_LINK_HOSTS and "iondistrict" not in host:
                    return href
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_display_name(raw: str) -> tuple[str, str | None, bool]:
    """Parse a raw listing display name into (clean_name, program, coming_soon).

    Examples:
      "Aikynetix – Nexus"           → ("Aikynetix", "Nexus", False)
      "Ampla – Nexus (Coming Soon)" → ("Ampla", "Nexus", True)
      "Ara Partners"                → ("Ara Partners", None, False)
      "Microsoft"                   → ("Microsoft", None, False)
    """
    coming_soon = bool(re.search(r"\(coming soon\)", raw, re.IGNORECASE))
    name = re.sub(r"\s*\(coming soon\)\s*", "", raw, flags=re.IGNORECASE).strip()
    # Split on em-dash (–), en-dash (—), or space-dash-space
    parts = re.split(r"\s*[–—]\s*|\s+-\s+", name, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip() or None, coming_soon
    return name.strip(), None, coming_soon


def _split_program_suffix(raw: str) -> tuple[str, str | None]:
    """Split a detail-page h1 name into (company_name, program).

    "Aikynetix - Nexus" → ("Aikynetix", "Nexus")
    "Ara Partners"      → ("Ara Partners", None)
    """
    parts = re.split(r"\s*[–—]\s*|\s+-\s+", raw, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip() or None
    return raw.strip(), None
