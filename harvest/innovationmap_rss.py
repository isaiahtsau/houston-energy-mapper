"""
InnovationMap Houston RSS harvester.

Harvests energy-tech company mentions from the InnovationMap Houston RSS feed.
InnovationMap is Houston's tech news publication; it covers funding rounds,
product launches, and startup news across all Houston tech verticals.

Source attribution: "InnovationMap Houston RSS"
Feed URL: https://houston.innovationmap.com/feeds/feed.rss

Design notes:
  - This is a recency-discovery harvester, not a directory harvester.
    Per-run yield is intentionally low (5-20 companies) because the value
    proposition is freshness: running weekly, this source surfaces energy
    startups at the moment of a funding announcement, product launch, or
    press event — often months before those companies appear in formal
    accelerator or portfolio listings. Over a quarter, accumulation
    reaches 20-50 unique companies that would not otherwise be discovered.

  - Two-pass per article:
      Pass 1 — energy filter: keep articles where the title contains at
               least one explicit energy keyword. Title-based filtering
               is more precise than category-based (categories in this
               feed are largely company names and reporter bylines, not
               topic taxonomy).
      Pass 2 — company extraction: from filtered articles, extract
               external <a href> links that point to company websites.
               Skip social media, news aggregators, and the publication's
               own subdomains. The link anchor text becomes the company
               name; the enclosing paragraph text becomes the description.

  - Deduplication across articles (same domain appearing in multiple
    articles) is handled by taking the first occurrence; cross-source
    dedup against other harvesters is handled at Step 10.

  - XML parsing uses stdlib xml.etree.ElementTree. BeautifulSoup (lxml)
    is used only for the CDATA description body.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
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
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# Energy keywords matched against article titles (lowercase).
# Deliberately strict — title-based matching has higher precision than
# category or body-text matching on this feed.
_ENERGY_TITLE_KEYWORDS: frozenset[str] = frozenset({
    "energy",
    "cleantech",
    "clean tech",
    "renewable",
    "solar",
    "wind",
    "hydrogen",
    "carbon",
    "geothermal",
    "battery",
    "oil and gas",
    "oil & gas",
    "decarbonization",
    "emissions",
    "fuel cell",
    "nuclear",
    "grid",
    "lng",
    "ccs",
    "carbon capture",
    "net zero",
    "net-zero",
    "superconductor",
    "superconductivity",
})

# Domains whose links are NOT company websites and should be skipped.
_SKIP_HOSTS: frozenset[str] = frozenset({
    # Social / professional networks
    "linkedin.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    # News & aggregators (not startup companies)
    "innovationmap.com",
    "houston.innovationmap.com",
    "energycapitalhtx.com",
    "morningstar.com",
    "axios.com",
    "businesswire.com",
    "prnewswire.com",
    "globenewswire.com",
    "sec.gov",
    "tracxn.com",
    "crunchbase.com",
    "pitchbook.com",
    "techcrunch.com",
    "forbes.com",
    "wsj.com",
    "bloomberg.com",
    "reuters.com",
    # Image / asset CDNs
    "rbl.ms",
    "assets.rebelmouse.io",
    # Accelerator platforms (not startups)
    "pilotathon.com",
    "energytechcypher.com",
    "cephyron.com",
    # Academic journals and university press offices (not companies)
    "pnas.org",
    "nature.com",
    "science.org",
    "uh.edu",
    "rice.edu",
    "tamu.edu",
    "utexas.edu",
    "mit.edu",
    "stanford.edu",
    "harvard.edu",
    # Government and regulatory
    "nasa.gov",
    "energy.gov",
    "doe.gov",
    "epa.gov",
})


def _is_company_url(href: str) -> bool:
    """Return True if href looks like a company website (not social/news/CDN)."""
    if not href or not href.startswith("http"):
        return False
    try:
        host = urlparse(href).netloc.lower().lstrip("www.")
        return not any(
            host == skip or host.endswith("." + skip)
            for skip in _SKIP_HOSTS
        )
    except Exception:
        return False


def _extract_companies_from_article(
    description_html: str,
    article_url: str,
    article_title: str,
    article_date: str,
    article_author: str,
    article_categories: list[str],
) -> list[RawCompanyRecord]:
    """Extract company records from a single article's CDATA description.

    Parses the HTML body of an RSS <description> field. For each external
    link pointing to a company website (not social/news/CDN), emits one
    RawCompanyRecord using the link anchor text as the company name and
    the enclosing paragraph text as the description.

    De-duplicates by domain within a single article. Returns an empty list
    if no qualifying links are found.
    """
    try:
        soup = BeautifulSoup(description_html, "lxml")
    except Exception as exc:
        logger.debug(f"[innovationmap:parse-fail] {article_url}: {exc}")
        return []

    seen_domains: set[str] = set()
    records: list[RawCompanyRecord] = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if not _is_company_url(href):
            continue

        try:
            domain = urlparse(href).netloc.lower().lstrip("www.")
        except Exception:
            continue

        if domain in seen_domains:
            continue
        seen_domains.add(domain)

        # Company name: anchor text, fallback to domain.
        # Skip generic link text that indicates a reference (not a company name).
        _GENERIC_ANCHORS = frozenset({
            "news release", "a news release", "the news release",
            "press release", "a press release",
            "here", "click here", "read more", "source",
            "report", "filing", "announcement",
        })
        name = anchor.get_text(strip=True)
        if not name or len(name) < 2 or name.lower() in _GENERIC_ANCHORS:
            # Derive a display name from the domain (e.g. "fervoenergy.com" → "fervoenergy.com")
            name = domain

        # Description: text of the enclosing <p> tag (strip HTML tags)
        para = anchor.find_parent("p")
        if para:
            desc = para.get_text(separator=" ", strip=True)
            # Trim to a useful length; very long paragraphs are article prose
            if len(desc) > 500:
                desc = desc[:500].rsplit(" ", 1)[0] + "…"
        else:
            desc = None

        records.append(
            RawCompanyRecord(
                name=name,
                source="InnovationMap Houston RSS",
                source_url=article_url,
                description=desc,
                website=href,
                tags=article_categories,
                extra={
                    "article_title": article_title,
                    "article_date": article_date,
                    "article_author": article_author,
                },
            )
        )

    return records


class InnovationMapRssHarvester(BaseHarvester):
    """Harvest energy startup mentions from InnovationMap Houston RSS feed.

    Per-run yield is intentionally low (5-20 unique companies). This source's
    value is real-time freshness over volume — running weekly, it surfaces
    energy startups at the moment of a funding announcement or product launch,
    often months before they appear in formal accelerator/portfolio listings.
    Accumulated quarterly yield is 20-50 unique companies.
    """

    SOURCE_NAME: ClassVar[str] = "InnovationMap Houston RSS"
    SOURCE_URL: ClassVar[str] = "https://houston.innovationmap.com/feeds/feed.rss"
    SOURCE_TYPE: ClassVar[str] = "rss_feed"
    UPDATE_CADENCE: ClassVar[str] = "daily"
    SCRAPE_METHOD: ClassVar[str] = "rss"
    AUTH_REQUIRED: ClassVar[bool] = False
    EXPECTED_YIELD: ClassVar[str] = "5-20"

    # XML namespaces present in the InnovationMap feed
    _NS = {
        "dc": "http://purl.org/dc/elements/1.1/",
        "media": "http://search.yahoo.com/mrss/",
        "content": "http://purl.org/rss/1.0/modules/content/",
        "atom": "http://www.w3.org/2005/Atom",
    }

    def fetch(self) -> list[RawCompanyRecord]:
        """Fetch RSS feed and extract energy-company records.

        Returns one RawCompanyRecord per unique company domain found in
        energy-matching articles. Empty list on HTTP error or malformed XML.
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
            logger.error(f"[innovationmap:fetch-error] {exc}")
            raise

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as exc:
            logger.error(f"[innovationmap:xml-parse-error] {exc}")
            return []

        items = root.findall(".//item")
        logger.info(f"[innovationmap:items] {len(items)} articles in feed")

        records: list[RawCompanyRecord] = []
        seen_domains: set[str] = set()  # cross-article dedup

        for item in items:
            title = (item.findtext("title") or "").strip()
            title_lower = title.lower()

            # Pass 1: energy filter on title
            if not any(kw in title_lower for kw in _ENERGY_TITLE_KEYWORDS):
                continue

            link = item.findtext("link") or ""
            pub_date = item.findtext("pubDate") or ""
            author = item.findtext(f"{{{self._NS['dc']}}}creator") or ""
            categories = [
                c.text.strip()
                for c in item.findall("category")
                if c.text and c.text.strip()
            ]
            description_html = item.findtext("description") or ""

            # Pass 2: extract company links from article body
            article_records = _extract_companies_from_article(
                description_html=description_html,
                article_url=link,
                article_title=title,
                article_date=pub_date,
                article_author=author,
                article_categories=categories,
            )

            for rec in article_records:
                try:
                    domain = urlparse(rec.website or "").netloc.lower().lstrip("www.")
                except Exception:
                    domain = ""

                if domain and domain not in seen_domains:
                    seen_domains.add(domain)
                    records.append(rec)
                    logger.debug(
                        f"[innovationmap:record] {rec.name!r} "
                        f"({domain}) from {title[:50]!r}"
                    )

        logger.info(
            f"[innovationmap:done] {len(records)} unique company records "
            f"from {len(items)} articles"
        )
        return records
