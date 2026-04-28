"""
Canonical ID generation for company records.

Companies are identified by a stable slug throughout the pipeline. The slug
lifecycle has two phases:

  Phase 1 — Provisional (at harvest time):
    slugify(company_name) → e.g. "cemvita-factory"
    Used until the enricher resolves a canonical domain.

  Phase 2 — Canonical (post-enrichment):
    slugify(canonical_domain) → e.g. "cemvita-com"
    Stable across renames. The dedup stage merges provisional-ID records
    that resolve to the same domain.

Both functions strip common legal suffixes (Inc., LLC, etc.) before slugifying
so that "Cemvita Factory Inc." and "Cemvita Factory" produce the same slug.
"""
from __future__ import annotations

import re

from slugify import slugify as _base_slugify

# Common legal entity suffixes to strip before slugifying.
# Applied case-insensitively; must be followed by end-of-string or punctuation.
_LEGAL_SUFFIXES = re.compile(
    r"\b(inc|llc|ltd|corp|corporation|co|company|holdings|group|ventures|"
    r"technologies|technology|tech|solutions|labs|laboratory|laboratories|"
    r"energy|capital|partners|associates|services|systems|international)\b\.?\s*$",
    re.IGNORECASE,
)


def canonical_id_from_domain(domain: str) -> str:
    """Generate a stable canonical company ID from a domain name.

    The domain is lowercased, www. prefix stripped, and slugified.
    This ID is stable even if the company renames — the domain rarely changes.

    Args:
        domain: Raw domain string, e.g. "www.Cemvita.com" or "cemvita.com".

    Returns:
        Slug string, e.g. "cemvita-com".

    Examples:
        canonical_id_from_domain("www.Cemvita.com")  → "cemvita-com"
        canonical_id_from_domain("ion.rice.edu")      → "ion-rice-edu"
    """
    domain = domain.lower().strip()
    domain = re.sub(r"^https?://", "", domain)   # strip protocol if present
    domain = re.sub(r"^www\.", "", domain)        # strip www.
    domain = domain.split("/")[0]                 # strip any path component
    return _base_slugify(domain, separator="-")


def provisional_id_from_name(name: str) -> str:
    """Generate a provisional company ID from a company name.

    Used before a canonical domain is known. Strips common legal suffixes
    so "Cemvita Factory Inc." and "Cemvita Factory" produce the same slug.

    Args:
        name: Raw company name as it appears in the source.

    Returns:
        Slug string, e.g. "cemvita-factory".

    Examples:
        provisional_id_from_name("Cemvita Factory Inc.")  → "cemvita-factory"
        provisional_id_from_name("Ion District")          → "ion-district"
    """
    name = _LEGAL_SUFFIXES.sub("", name).strip().strip(",").strip()
    return _base_slugify(name, separator="-")


def normalize_name(name: str) -> str:
    """Return a lowercased, suffix-stripped company name for dedup comparison.

    Used by dedupe/matcher.py to produce the string passed to rapidfuzz.
    Does not slugify — preserves spaces so fuzzy matching works on word tokens.

    Args:
        name: Raw company name.

    Returns:
        Normalized string, e.g. "cemvita factory".
    """
    name = _LEGAL_SUFFIXES.sub("", name).strip().strip(",").strip()
    return name.lower()
