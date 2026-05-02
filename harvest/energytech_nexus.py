"""
Energytech Nexus (formerly EnergyTech Nexus) press-release harvester.

Harvests company records from EnergyCapitalHTX press release articles covering
the Energytech Nexus / Energytech Cypher COPILOT accelerator and Pilotathon.

Source attribution: "Energytech Nexus"

Background:
  EnergyTech Nexus rebranded as Energytech Cypher (ETC) in March 2026.
  The organization's member directory (energytechcypher.com/members) is behind
  Memberstack authentication and not publicly accessible.

  However, all cohort companies are named in static EnergyCapitalHTX press
  release articles, which load without auth or JS rendering. This harvester
  fetches those known article URLs directly.

Articles harvested (per live inspection 2026-05-02):
  - COPILOT 2025 cohort (14 companies):
    https://energycapitalhtx.com/energytech-nexus-copilot-cohort-2025
  - Pilotathon 2025 pitch companies (9 additional):
    https://energycapitalhtx.com/energy-tech-nexus-2025-pilotathon

Parsing strategy:
  Article content is in <article> tag. Companies are in <li> elements.
  Nav/related-article links end with '›' (right arrow) and are excluded.
  The COPILOT article also repeats 14 COPILOT company names (name-only, no
  description) in the Pilotathon article — these are deduplicated by name.

  Each <li> with a description follows the pattern:
    "[Location-based ]CompanyName, which/that/developer of/etc. <description>"
  or (for Houston companies, no location prefix):
    "CompanyName, <description>"

  Company name is extracted by splitting on the first comma where the text
  before the comma does not end with a two-letter location abbreviation
  pattern (e.g. "Alabama-based Accelerate Wind").

Expected yield: 20-25 records (14 COPILOT + 9 Pilotathon-only; ~3 overlap).
"""
from __future__ import annotations

import logging
import re
from typing import ClassVar

import requests
from bs4 import BeautifulSoup

from harvest.base import BaseHarvester, RawCompanyRecord

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Articles to harvest — ordered so COPILOT (richer descriptions) is processed first.
# Duplicate companies (COPILOT names appear name-only in Pilotathon article) are
# deduplicated by normalized name.
_ARTICLE_URLS: list[tuple[str, str]] = [
    (
        "https://energycapitalhtx.com/energytech-nexus-copilot-cohort-2025",
        "COPILOT 2025",
    ),
    (
        "https://energycapitalhtx.com/energy-tech-nexus-2025-pilotathon",
        "Pilotathon 2025",
    ),
]

# <li> items ending with this character are navigation/related-article links,
# not company entries.
_NAV_LINK_SUFFIX = "\u203a"   # › (right single angle quotation mark)

# Regex to strip a leading "Location-based " prefix from a company entry.
# e.g. "Birmingham, Alabama-based Accelerate Wind" → "Accelerate Wind, ..."
# e.g. "Calgary, Canada-based Harber Coatings" → "Harber Coatings, ..."
# e.g. "Phoenix-based EarthEn Energy" → "EarthEn Energy, ..."
_LOCATION_PREFIX_RE = re.compile(
    r"^(?:[A-Z][^,]+,\s+)?[A-Z][a-z][\w\s]*[-\u2013]based\s+",
    re.UNICODE,
)

# Regex to strip "housed at Houston's Greentown Labs" from Houston entries
_GREENTOWN_CLAUSE_RE = re.compile(
    r",?\s+housed at [^,.]+",
    re.IGNORECASE,
)


def _normalize_name(name: str) -> str:
    """Lowercase + strip for deduplication. Strips corporate suffixes (Inc., LLC, etc.)."""
    normalized = re.sub(r"[\s,.\-&]+", " ", name.lower()).strip()
    # Strip trailing corporate suffixes so "PolyQor" == "PolyQor Inc."
    normalized = re.sub(r"\s+(inc\.?|llc\.?|ltd\.?|corp\.?|co\.?)$", "", normalized).strip()
    return normalized


def _is_duplicate(key: str, seen_names: set[str]) -> bool:
    """Return True if key matches an entry in seen_names exactly or as a word-prefix.

    Handles cases like "EarthEn" (pilotathon repeat) vs "EarthEn Energy" (COPILOT full
    name): one normalized form is a word-boundary prefix of the other.
    """
    if key in seen_names:
        return True
    # Prefix check: "earthen" should match "earthen energy"
    for seen in seen_names:
        short, long = (key, seen) if len(key) <= len(seen) else (seen, key)
        if len(short) >= 5 and long.startswith(short + " "):
            return True
    return False


def _parse_company_li(li_text: str) -> tuple[str, str | None, str | None] | None:
    """Parse one <li> text into (name, description, location_raw).

    Returns None if the item looks like a nav link or is too short to be a
    company entry.

    Parsing steps:
      1. Reject items ending with '›' (nav/related-article links).
      2. Extract optional location prefix → location_raw.
      3. Split remaining text on first comma: name vs. description.
      4. Clean up 'housed at Greentown' clause from Houston entries.
    """
    text = li_text.strip()

    # Nav link filter
    if text.endswith(_NAV_LINK_SUFFIX) or text.endswith(">"):
        return None

    # Too short to be a meaningful company entry
    if len(text) < 5:
        return None

    # Extract location prefix if present
    loc_match = _LOCATION_PREFIX_RE.match(text)
    location_raw: str | None = None
    if loc_match:
        location_raw = loc_match.group(0).rstrip("- ").strip()
        # Trim the trailing "X-based" word from the location string
        location_raw = re.sub(r"-based$", "", location_raw).strip()
        text = text[loc_match.end():]

    # Remove "housed at Greentown Labs" clause from Houston-first entries
    text = _GREENTOWN_CLAUSE_RE.sub("", text).strip()

    # Split on first comma to get name vs description
    if "," in text:
        name_part, _, desc_part = text.partition(",")
        name = name_part.strip()
        description = desc_part.strip().lstrip("which that is ").strip() or None
    else:
        # No comma — name-only entry (e.g. "GeoFuels" in the Pilotathon repeat list)
        name = text
        description = None

    if not name:
        return None

    return name, description, location_raw


class EnergyTechNexusHarvester(BaseHarvester):
    """Harvest company records from Energytech Nexus press release articles.

    Fetches known EnergyCapitalHTX article URLs and parses <li> elements
    from the <article> content block. Deduplicates by normalized company name
    (COPILOT article is processed first; name-only Pilotathon repeats are dropped).

    Returns one RawCompanyRecord per unique company. Extra fields:
      program: "COPILOT 2025" | "Pilotathon 2025"
      article_url: URL of the article where the company was first found
    """

    SOURCE_NAME: ClassVar[str] = "Energytech Nexus"
    SOURCE_URL: ClassVar[str] = "https://energycapitalhtx.com"
    SOURCE_TYPE: ClassVar[str] = "accelerator"
    UPDATE_CADENCE: ClassVar[str] = "annual"
    SCRAPE_METHOD: ClassVar[str] = "static"
    AUTH_REQUIRED: ClassVar[bool] = False
    EXPECTED_YIELD: ClassVar[str] = "20-25"

    def fetch(self) -> list[RawCompanyRecord]:
        """Fetch both article URLs and return deduplicated company records."""
        seen_names: set[str] = set()
        records: list[RawCompanyRecord] = []

        for url, program in _ARTICLE_URLS:
            self.rate_limiter.wait()
            page_records = self._fetch_article(url, program, seen_names)
            records.extend(page_records)
            logger.info(
                f"[energytech:article] {program}: {len(page_records)} new companies "
                f"(total so far: {len(records)})"
            )

        logger.info(f"[energytech:done] {len(records)} total records extracted")
        return records

    def _fetch_article(
        self,
        url: str,
        program: str,
        seen_names: set[str],
    ) -> list[RawCompanyRecord]:
        """Fetch one article URL and parse company <li> items.

        Skips companies already in seen_names (deduplication across articles).
        Adds newly found normalized names to seen_names.
        """
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error(f"[energytech:fetch-error] {url}: {exc}")
            raise

        soup = BeautifulSoup(resp.text, "lxml")
        article = soup.select_one("article")
        if not article:
            logger.warning(
                f"[energytech:no-article] {url}: <article> element not found — "
                "page structure may have changed"
            )
            return []

        records: list[RawCompanyRecord] = []
        for li in article.select("li"):
            li_text = li.get_text(separator=" ", strip=True)
            parsed = _parse_company_li(li_text)
            if parsed is None:
                continue

            name, description, location_raw = parsed
            key = _normalize_name(name)
            if _is_duplicate(key, seen_names):
                logger.debug(f"[energytech:dedup] {name!r} already seen — skipped")
                continue

            seen_names.add(key)
            records.append(
                RawCompanyRecord(
                    name=name,
                    source=self.SOURCE_NAME,
                    source_url=url,
                    description=description,
                    website=None,   # not in article text
                    location_raw=location_raw,
                    tags=[],
                    extra={
                        "program": program,
                        "article_url": url,
                    },
                )
            )
            logger.debug(
                f"[energytech:record] {name!r} ({program}) loc={location_raw!r}"
            )

        return records
