"""
Rice Energy Tech Venture Forum (ETVF) harvester.

Harvests company records from the annual Rice Alliance Energy Tech Venture
Forum, held each September in Houston. The ETVF surfaces companies from
multiple programs: the Rice Alliance Clean Energy Accelerator (RACEA),
Halliburton Labs, Greentown Houston, and international energy-tech presenters.

Source attribution: "presented at ETVF". RACEA-specific membership is resolved
by cross-source dedup at Step 10 against the rice_alliance_racea harvester
(deferred to Step 7+; requires Playwright — ricecleanenergy.org is JS-rendered).

Page structure notes (per live-site inspection 2026-04-30):
  - 2024+: Grid card layout (article.cc--component-container.cc--profile-card).
    Each card links to a /person/{slug} profile page with name, website URL,
    and description. Profile affiliation shows "Presenting Company" or
    "Office Hours Company" only — no RACEA class marker on alliance.rice.edu.
  - 2022-2023: Two text-list formats:
      Pattern A (2022): ul.links-container with li > a[href="http://..."] (direct URLs)
      Pattern B (2023): div.f--field.f--wysiwyg > ul > li > a (LinkedIn or direct)
    No /person/ profile pages exist for these years; records are listing_only.
  - 2021: ETVF page returns 404 (first ETVF was 2022).

Expected yield: 80-120 records across ETVF_YEARS after within-harvest dedup.
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

# Hosts treated as non-company URLs (social, nav, internal)
_SKIP_HOSTS: frozenset[str] = frozenset({
    "linkedin.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "alliance.rice.edu",
    "rice.edu",
    "ricecleanenergy.org",
    "ricecleanenergy.com",
    "news.rice.edu",
})

# Footer/nav wysiwyg blocks contain these strings — skip them during description extraction
_WYSIWYG_SKIP_FRAGMENTS: tuple[str, ...] = (
    "Contact Us:",
    "Email:alliance@rice.edu",
    "Copyright ©",
    "Sign up for our weekly",
)


def _is_company_url(href: str) -> bool:
    """Return True if href looks like a direct company website, not social/nav."""
    if not href or not href.startswith("http"):
        return False
    try:
        host = urlparse(href).netloc.lower().lstrip("www.")
        return not any(
            host == skip or host.endswith("." + skip) for skip in _SKIP_HOSTS
        )
    except Exception:
        return False


def _clean_name(raw: str) -> str:
    """Strip trailing separator artifacts (' -' or ' .') from profile-page names.

    alliance.rice.edu profiles append ' -' (pre-2025) or ' .' (2025+) as a visual
    separator between the company name and the following field. Both are stripped.
    """
    return raw.strip().rstrip("-.").strip()


class RiceEtvfHarvester(BaseHarvester):
    """Harvests company records from the Rice Energy Tech Venture Forum (ETVF).

    Two-pass strategy:
      Pass 1 — listing pages: collect candidates from each ETVF year's /Companies
               page. 2024+ yields {name, slug} tuples; 2022-2023 yields
               {name, website} tuples (no profile pages for older years).
      Pass 2 — profile pages: fetch /person/{slug} for 2024+ candidates to get
               website URL and description. Listing-only records are emitted
               directly for 2022-2023 and any 2024 profiles that 404.

    Within-harvest dedup: keyed on slug (profile candidates) or normalized
    company name (listing-only). Cross-source dedup at Step 10 collapses
    overlap with rice_alliance_racea and other sources.

    Listing-only / profile duplicates: a company may appear as listing-only
    (no slug, from a 2022-2023 text list) and as a profile record (with slug,
    from a 2024+ grid card). Within-harvest dedup keys on slug-or-name, so these
    produce two separate records. Cross-source dedup at Step 10 handles merging
    via name fuzzy matching.

    Source data quality: alliance.rice.edu profile pages occasionally contain
    another company's description due to CMS copy-paste errors (confirmed on
    NuCube Energy / 2024 cohort). These are source-side anomalies — our parser
    is correct. Records where the company name's first word does not appear in the
    first 100 chars of the description receive extra["source_data_quality_flag"] =
    "description_company_name_mismatch_possible" to help downstream LLM sub-sector
    classification tolerate stale or wrong descriptions.
    """

    SOURCE_NAME: ClassVar[str] = "Rice Energy Tech Venture Forum (ETVF)"
    SOURCE_URL: ClassVar[str] = "https://alliance.rice.edu/etvf/past-conferences"
    SOURCE_TYPE: ClassVar[str] = "event"
    UPDATE_CADENCE: ClassVar[str] = "annual"
    SCRAPE_METHOD: ClassVar[str] = "static_html"
    AUTH_REQUIRED: ClassVar[bool] = False
    EXPECTED_YIELD: ClassVar[str] = "80-240"

    # Years to harvest. Extend this list when future ETVF years are published —
    # no other Python changes needed. 2021 returns 404 (first ETVF was 2022).
    ETVF_YEARS: ClassVar[list[int]] = [2022, 2023, 2024, 2025]

    _LISTING_URL: ClassVar[str] = (
        "https://alliance.rice.edu/etvf/past-conferences/{year}-etvf/Companies"
    )
    _PROFILE_URL: ClassVar[str] = "https://alliance.rice.edu/person/{slug}"

    def fetch(self) -> list[RawCompanyRecord]:
        """Two-pass harvest: listing pages then profile pages.

        Returns:
            List of RawCompanyRecord, one per unique company across all ETVF years.
            Empty list is valid if all listing pages fail.
        """
        # candidates: dedup_key → {name, slug, website, etvf_years, listing_only}
        candidates: dict[str, dict] = {}

        # Also probe the next year in case it has been published since ETVF_YEARS was set
        probe_year = max(self.ETVF_YEARS) + 1
        years_to_try = list(self.ETVF_YEARS) + [probe_year]

        # ── Pass 1: listing pages ─────────────────────────────────────────────
        for year in years_to_try:
            url = self._LISTING_URL.format(year=year)
            self.rate_limiter.wait()
            try:
                resp = requests.get(url, headers=_HEADERS, timeout=15)
            except requests.RequestException as exc:
                logger.warning(f"[rice_etvf] Network error fetching {url}: {exc}")
                continue

            if resp.status_code == 404:
                if year not in self.ETVF_YEARS:
                    logger.debug(
                        f"[rice_etvf] {year} ETVF page not yet published (404) — skipping"
                    )
                else:
                    logger.warning(
                        f"[rice_etvf] Expected year {year} returned 404: {url}"
                    )
                continue

            if resp.status_code != 200:
                logger.warning(
                    f"[rice_etvf] Unexpected HTTP {resp.status_code} for {url} — skipping"
                )
                continue

            soup = BeautifulSoup(resp.text, "lxml")

            if year >= 2024:
                year_cands = self._parse_grid_listing(soup, year)
            else:
                year_cands = self._parse_text_listing(soup, year)

            logger.info(
                f"[rice_etvf] {year} listing: {len(year_cands)} candidates"
            )

            for cand in year_cands:
                slug = cand.get("slug")
                key = slug if slug else cand["name"].lower().strip()
                if key in candidates:
                    candidates[key]["etvf_years"].append(year)
                else:
                    cand["etvf_years"] = [year]
                    candidates[key] = cand

        logger.info(
            f"[rice_etvf] Pass 1 complete: {len(candidates)} unique candidates"
        )

        # ── Pass 2: profile pages ─────────────────────────────────────────────
        records: list[RawCompanyRecord] = []

        for cand in candidates.values():
            slug = cand.get("slug")
            if not slug:
                # 2022-2023 listing-only: emit directly, no profile fetch needed
                records.append(self._make_listing_only(cand))
                continue

            profile_url = self._PROFILE_URL.format(slug=slug)
            self.rate_limiter.wait()
            try:
                resp = requests.get(profile_url, headers=_HEADERS, timeout=15)
            except requests.RequestException as exc:
                logger.warning(
                    f"[rice_etvf] Network error fetching profile {profile_url}: {exc}"
                )
                records.append(self._make_listing_only(cand))
                continue

            if resp.status_code != 200:
                logger.warning(
                    f"[rice_etvf] Profile {profile_url} returned {resp.status_code}"
                    " — emitting listing-only record"
                )
                records.append(self._make_listing_only(cand))
                continue

            profile_soup = BeautifulSoup(resp.text, "lxml")
            records.append(self._extract_profile(profile_soup, cand))

        logger.info(f"[rice_etvf] Pass 2 complete: {len(records)} records total")
        return records

    # ── Listing parsers ───────────────────────────────────────────────────────

    def _parse_grid_listing(self, soup: BeautifulSoup, year: int) -> list[dict]:
        """Parse 2024+ grid card layout (article.cc--component-container.cc--profile-card).

        Each card contains h3 > a[href="/person/{slug}"] with the company name
        and profile slug. Cards without a /person/ link are skipped.

        Args:
            soup: Parsed listing page HTML.
            year: ETVF year (used for logging only).

        Returns:
            List of candidate dicts: {name, slug, website: None, listing_only: False}.
        """
        cards = soup.select("article.cc--component-container.cc--profile-card")
        results = []
        for card in cards:
            link = card.select_one("h3 a[href^='/person/']")
            if not link:
                continue
            slug = link["href"].split("/person/")[-1].strip("/")
            name = _clean_name(link.get_text(strip=True))
            if not name or not slug:
                continue
            results.append({
                "name": name,
                "slug": slug,
                "website": None,
                "listing_only": False,
            })
        return results

    def _parse_text_listing(self, soup: BeautifulSoup, year: int) -> list[dict]:
        """Parse 2022-2023 text-list formats. Two structural patterns:

        Pattern A (2022): ul.links-container > li > a (direct company URLs)
        Pattern B (2023): div.f--field.f--wysiwyg > ul > li > a (LinkedIn or direct)

        Both patterns are tried. Results are deduped by normalized name within
        the page. Skips empty/short names and internal rice.edu links.

        Args:
            soup: Parsed listing page HTML.
            year: ETVF year (used for logging only).

        Returns:
            List of candidate dicts: {name, slug: None, website, listing_only: True}.
        """
        seen: set[str] = set()
        results = []

        def _add(name: str, href: str) -> None:
            name = name.strip()
            if not name or len(name) < 3:
                return
            if "rice.edu" in href or "ricecleanenergy" in href:
                return
            key = name.lower()
            if key in seen:
                return
            seen.add(key)
            is_company = _is_company_url(href)
            results.append({
                "name": name,
                "slug": None,
                "website": href if is_company else None,
                "listing_only": True,
            })

        # Pattern A: ul.links-container (2022)
        for ul in soup.select("ul.links-container"):
            for li in ul.select("li"):
                a = li.select_one("a[href]")
                if a:
                    _add(a.get_text(strip=True), a.get("href", ""))

        # Pattern B: wysiwyg rich-text blocks containing company link lists (2023)
        for wysiwyg in soup.select("div.f--field.f--wysiwyg"):
            # Skip footer/contact wysiwyg blocks
            block_text = wysiwyg.get_text(" ", strip=True)
            if any(frag in block_text for frag in _WYSIWYG_SKIP_FRAGMENTS):
                continue
            for li in wysiwyg.select("ul li"):
                a = li.select_one("a[href]")
                if a:
                    _add(a.get_text(strip=True), a.get("href", ""))

        return results

    # ── Profile extractor ────────────────────────────────────────────────────

    def _extract_profile(
        self, soup: BeautifulSoup, cand: dict
    ) -> RawCompanyRecord:
        """Extract structured fields from a /person/{slug} profile page.

        Field selectors (verified against live pages 2026-04-30):
          name:        First .f--field.f--text in header region (stripped of trailing ' -')
          website:     First a.button--alt[href^="http"] not pointing to rice.edu
          description: First div.f--field.f--wysiwyg that is not a footer/contact block
          affiliation: Second .f--field.f--text (e.g. "Presenting Company")

        Falls back to listing-supplied name if the profile name is missing.

        Args:
            soup: Parsed profile page HTML.
            cand: Candidate dict from Pass 1.

        Returns:
            RawCompanyRecord with all available fields populated.
        """
        # Name
        name_tags = soup.select(
            ".header-container .f--field.f--text, .title-hero .f--field.f--text"
        )
        name = cand["name"]  # fallback to listing name
        if name_tags:
            candidate = _clean_name(name_tags[0].get_text(strip=True))
            if candidate:
                name = candidate

        # Website: first button--alt link outside rice.edu
        website: str | None = None
        for a in soup.select("a.button--alt[href]"):
            href = a.get("href", "")
            if _is_company_url(href):
                website = href
                break

        # Description: first wysiwyg not matching footer/contact patterns
        description: str | None = None
        for wysiwyg in soup.select("div.f--field.f--wysiwyg"):
            text = wysiwyg.get_text(" ", strip=True)
            if any(frag in text for frag in _WYSIWYG_SKIP_FRAGMENTS):
                continue
            if text:
                description = text
                break

        # Affiliation text (e.g. "Presenting Company")
        affiliation_raw: str | None = None
        if len(name_tags) >= 2:
            affiliation_raw = name_tags[1].get_text(strip=True)

        # Data quality flag: if the first word of the company name doesn't appear in
        # the first 100 chars of the description, the CMS may have a copy-paste error.
        quality_flag: str | None = None
        if description:
            first_word = name.split()[0].lower() if name else ""
            if first_word and first_word not in description[:100].lower():
                quality_flag = "description_company_name_mismatch_possible"

        slug = cand.get("slug", "")
        return RawCompanyRecord(
            name=name,
            source=self.SOURCE_NAME,
            source_url=self._PROFILE_URL.format(slug=slug) if slug else None,
            description=description,
            website=website,
            location_raw=None,  # not available on alliance.rice.edu profile pages
            tags=[],
            extra={
                "cohort_class": None,  # RACEA class not exposed on alliance.rice.edu
                "etvf_years": cand.get("etvf_years", []),
                "affiliation_raw": affiliation_raw,
                "listing_only": False,
                "source_data_quality_flag": quality_flag,
            },
        )

    def _make_listing_only(self, cand: dict) -> RawCompanyRecord:
        """Emit a minimal record for a candidate without a fetchable profile page.

        Used for:
          - 2022-2023 text-list companies (no /person/ slug exists)
          - 2024 companies whose /person/ page returned a non-200 status

        The listing_only flag tells downstream classifier and dedup layers
        that this record may have a richer twin from another source.

        Args:
            cand: Candidate dict from Pass 1.

        Returns:
            RawCompanyRecord with name and (if available) website populated.
        """
        return RawCompanyRecord(
            name=cand["name"],
            source=self.SOURCE_NAME,
            source_url=None,
            description=None,
            website=cand.get("website"),
            location_raw=None,
            tags=[],
            extra={
                "cohort_class": None,
                "etvf_years": cand.get("etvf_years", []),
                "affiliation_raw": None,
                "listing_only": True,
            },
        )
