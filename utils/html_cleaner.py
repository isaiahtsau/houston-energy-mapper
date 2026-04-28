"""
HTML text extraction and normalization utilities.

Used by harvesters to strip markup from scraped content before passing
text to the classifier or enricher. Keeping these functions centralized
ensures consistent text normalization across all sources.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

# Common legal suffix pattern for company name cleaning (mirrors utils/slugify.py)
_LEGAL_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|co|company|holdings|group|ventures|"
    r"technologies|technology|tech|solutions|labs|laboratory|laboratories|"
    r"energy|capital|partners|associates|services|systems|international)\b\.?\s*$",
    re.IGNORECASE,
)

# Whitespace normalization: collapse runs of whitespace (including newlines) to single space
_WHITESPACE_RE = re.compile(r"\s+")

# URL pattern for stripping stray URLs from extracted text
_URL_RE = re.compile(r"https?://\S+")


def strip_html(html: str) -> str:
    """Strip all HTML tags and return plain text.

    Uses lxml for speed. Extracts text with a single space as the separator
    between tags, so adjacent block elements don't merge their text.

    Args:
        html: Raw HTML string (may be a full page or a fragment).

    Returns:
        Plain text string with HTML tags removed.
    """
    soup = BeautifulSoup(html, "lxml")
    return soup.get_text(separator=" ", strip=True)


def normalize_whitespace(text: str) -> str:
    """Collapse multiple spaces, tabs, and newlines into a single space.

    Args:
        text: Input string with arbitrary whitespace.

    Returns:
        String with all internal whitespace runs collapsed to a single space,
        leading and trailing whitespace removed.
    """
    return _WHITESPACE_RE.sub(" ", text).strip()


def clean_description(html_or_text: str, max_chars: int = 2000) -> str:
    """Extract and normalize a company description from HTML or plain text.

    Combines strip_html + normalize_whitespace, then truncates to max_chars
    with a word boundary so the classifier never receives a truncated word.

    Args:
        html_or_text: Raw HTML or plain text description.
        max_chars:    Maximum character length of the returned string.
                      2000 is generous for a classifier; reduce for cost savings.

    Returns:
        Clean, truncated plain text description.
    """
    text = strip_html(html_or_text) if "<" in html_or_text else html_or_text
    text = normalize_whitespace(text)
    if len(text) > max_chars:
        # Truncate at a word boundary
        truncated = text[:max_chars].rsplit(" ", 1)[0]
        return truncated + "…"
    return text


def clean_company_name(name: str) -> str:
    """Remove common legal suffixes and normalize whitespace for a company name.

    Used when a source includes "XYZ Technologies, Inc." and we want "XYZ Technologies"
    for display and dedup purposes. Does not slugify — use utils/slugify.py for IDs.

    Args:
        name: Raw company name as it appears in the source.

    Returns:
        Cleaned company name string.
    """
    name = normalize_whitespace(name)
    name = _LEGAL_SUFFIXES.sub("", name).strip().strip(",").strip()
    return name


def extract_domain_from_url(url: str) -> str | None:
    """Extract the bare domain from a URL string.

    Args:
        url: Full URL, e.g. "https://www.cemvita.com/about".

    Returns:
        Domain string, e.g. "cemvita.com", or None if url is empty/malformed.
    """
    if not url:
        return None
    url = url.strip()
    # Strip protocol and www.
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^www\.", "", url)
    domain = url.split("/")[0].split("?")[0].split("#")[0]
    return domain.lower() if domain else None
