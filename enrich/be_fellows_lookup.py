"""
BE Fellows lookup — structured reference data from data/reference/be_fellows_2026_raw.txt.

Parses the raw BE Fellows 2026 roster into a structured dict keyed by
normalized company name. Exposes a fuzzy-match lookup function for use
in founder pedigree enrichment (B4 signal injection).

Data file format (blank-line-separated blocks):
  Innovator Fellows section:
    <Fellow Name>
    <Company Name>          ← "Business Fellow" = no real company; skip
    <Role>
  Business Fellows section — same format, company always "Business Fellow"; skip

Edge case: some Innovator Fellows are also Business Fellows (2-line blocks,
company = "Business Fellow", no role line). These are skipped.

Saved JSON schema: data/reference/be_fellows_structured.json
  {
    "companies": {
      "<normalized_name>": {
        "canonical_name": str,
        "fellows": [{"name": str, "role": str}]
      }
    },
    "meta": {
      "total_fellows": int,
      "total_companies": int,
      "source_file": str,
      "generated_at": str
    }
  }
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)

_RAW_FILE = Path(__file__).parent.parent / "data" / "reference" / "be_fellows_2026_raw.txt"
_STRUCTURED_FILE = (
    Path(__file__).parent.parent / "data" / "reference" / "be_fellows_structured.json"
)

_FUZZY_THRESHOLD = 0.85

_SECTION_HEADERS: frozenset[str] = frozenset({"Innovator Fellows", "Business Fellows"})


# ── TypedDicts ─────────────────────────────────────────────────────────────────

class FellowRecord(TypedDict):
    name: str
    role: str


class FellowLookupResult(TypedDict):
    name: str
    role: str
    company: str
    match_type: str  # "exact" or "fuzzy"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _normalize(name: str) -> str:
    """Lowercase, strip non-alphanumeric, collapse whitespace."""
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


# ── Parsing ────────────────────────────────────────────────────────────────────

def parse_raw_file(raw_path: Path | None = None) -> dict[str, dict]:
    """Parse the raw BE Fellows text file into a structured companies dict.

    Splits the file on blank lines to get per-fellow blocks. Each block has
    2–3 non-blank lines: Name, Company (optional role on third line).
    Blocks where Company == "Business Fellow" are skipped.

    Returns:
        {
          normalized_company_name: {
            "canonical_name": str,
            "fellows": [{"name": str, "role": str}]
          }
        }
    """
    path = raw_path or _RAW_FILE
    text = path.read_text(encoding="utf-8")

    # Split on one or more blank lines → paragraph-level blocks
    raw_blocks = re.split(r"\n\s*\n", text.strip())

    companies: dict[str, dict] = {}

    for block in raw_blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue

        # Skip section headers
        if lines[0] in _SECTION_HEADERS:
            continue

        if len(lines) < 2:
            # Single-line block — can't be a fellow entry
            continue

        fellow_name = lines[0]
        company_name = lines[1]
        role = lines[2] if len(lines) >= 3 else "Business Fellow"

        # Skip if no real company
        if company_name.lower() == "business fellow":
            continue

        normalized = _normalize(company_name)
        if normalized not in companies:
            companies[normalized] = {"canonical_name": company_name, "fellows": []}

        companies[normalized]["fellows"].append(
            FellowRecord(name=fellow_name, role=role)
        )

    total_fellows = sum(len(v["fellows"]) for v in companies.values())
    logger.info(
        f"[be_fellows_lookup:parsed] {total_fellows} fellows across "
        f"{len(companies)} companies"
    )
    return companies


def save_structured_json(
    companies: dict[str, dict],
    output_path: Path | None = None,
) -> Path:
    """Persist the structured companies dict to JSON and return the path."""
    path = output_path or _STRUCTURED_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    total_fellows = sum(len(v["fellows"]) for v in companies.values())
    payload = {
        "companies": companies,
        "meta": {
            "total_fellows": total_fellows,
            "total_companies": len(companies),
            "source_file": str(_RAW_FILE),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(
        f"[be_fellows_lookup:saved] {path} "
        f"({total_fellows} fellows, {len(companies)} companies)"
    )
    return path


# ── Module-level cache ──────────────────────────────────────────────────────────

_COMPANIES_CACHE: dict[str, dict] | None = None


def _load_companies() -> dict[str, dict]:
    """Return companies dict, loading from JSON cache or raw file on first call."""
    global _COMPANIES_CACHE
    if _COMPANIES_CACHE is not None:
        return _COMPANIES_CACHE

    if _STRUCTURED_FILE.exists():
        data = json.loads(_STRUCTURED_FILE.read_text(encoding="utf-8"))
        _COMPANIES_CACHE = data["companies"]
    else:
        _COMPANIES_CACHE = parse_raw_file()

    return _COMPANIES_CACHE


def _reset_cache() -> None:
    """Reset the module-level cache (used in tests)."""
    global _COMPANIES_CACHE
    _COMPANIES_CACHE = None


# ── Public API ──────────────────────────────────────────────────────────────────

def lookup_company_for_fellow_match(company_name: str) -> list[FellowLookupResult]:
    """Return BE Fellows associated with *company_name*.

    Tries exact match on normalized name first, then fuzzy match at
    threshold 0.85 (SequenceMatcher ratio).

    Args:
        company_name: Company name as it appears in pipeline data.

    Returns:
        List of FellowLookupResult dicts (name, role, company, match_type).
        Empty list if no match found.
    """
    if not company_name or not company_name.strip():
        return []

    companies = _load_companies()
    normalized = _normalize(company_name)

    # Exact match
    if normalized in companies:
        entry = companies[normalized]
        return [
            FellowLookupResult(
                name=f["name"],
                role=f["role"],
                company=entry["canonical_name"],
                match_type="exact",
            )
            for f in entry["fellows"]
        ]

    # Fuzzy match — find best-scoring key
    best_score = 0.0
    best_key: str | None = None
    for key in companies:
        score = SequenceMatcher(None, normalized, key).ratio()
        if score > best_score:
            best_score = score
            best_key = key

    if best_score >= _FUZZY_THRESHOLD and best_key is not None:
        entry = companies[best_key]
        logger.debug(
            f"[be_fellows_lookup:fuzzy] '{company_name}' → '{entry['canonical_name']}' "
            f"(score={best_score:.2f})"
        )
        return [
            FellowLookupResult(
                name=f["name"],
                role=f["role"],
                company=entry["canonical_name"],
                match_type="fuzzy",
            )
            for f in entry["fellows"]
        ]

    return []
