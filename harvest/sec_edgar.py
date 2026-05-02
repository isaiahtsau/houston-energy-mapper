"""
SEC EDGAR Form D harvester — Houston-based issuers.

Harvests Form D filings where biz_locations contains "Houston, TX" from the
SEC EDGAR Full-Text Search (EFTS) API. Form D is the notice of exempt offering
of securities; Houston-based issuers must file within 15 days of first sale.

Access pattern (per live-site inspection 2026-05-02):
  - API: https://efts.sec.gov/LATEST/search-index
  - Full-text search for "Houston, TX" restricted to Form D filings.
  - Auth: None. SEC Fair Access policy requires a declared User-Agent
    (company name + contact email). Max 10 req/s.
  - Pagination: `from` offset, 100 hits per page.

Response structure (per hit):
  _source.display_names: ["COMPANY NAME  (TICKER)  (CIK XXXXXXXXXX)"]
  _source.adsh:          "0001234567-24-000001"  (accession number)
  _source.ciks:          ["0001234567"]
  _source.file_date:     "2024-03-18"
  _source.biz_locations: ["Houston, TX"]
  _source.items:         ["06B"]   (Form D section codes)

Company name is extracted from display_names by stripping the trailing
"(TICKER) (CIK ...)" suffix. Source URL is the EDGAR filing index page.

Law firm flag: Filings where the listed entity name is a well-known Houston
law firm are flagged with extra["form_d_filed_by_law_firm"] = True.
In those cases, the filer address (e.g., 910 Louisiana St) reflects the
law firm, not the issuer. The `form_d_houston_address` signal MUST NOT
be credited for flagged records. This flag is a best-effort string check;
full verification requires parsing the individual filing XML.

Date range: Rolling 12 months from today's date, shifted back 30 days to
allow for late-filed amendments. Configurable via constructor kwargs.

Expected yield: 80-120 raw records per annual run (~800-900 Houston Form D
filings per year; ~80-120 in venture-scale-relevant offering categories).
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import ClassVar

import requests

from harvest.base import BaseHarvester, RawCompanyRecord

logger = logging.getLogger(__name__)

_EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
_HEADERS = {
    # SEC Fair Access policy: must identify the requester
    "User-Agent": "IonTakeHome research@ion.com",
    "Accept": "application/json",
}

_PAGE_SIZE = 100

# Known Houston law firms that file Form D on behalf of clients.
# Filings from these entities have issuer address = law firm address, not
# actual issuer. extra["form_d_filed_by_law_firm"] = True is set for these.
_LAW_FIRM_KEYWORDS: frozenset[str] = frozenset({
    "vinson", "elkins", "baker botts", "norton rose", "fulbright",
    "latham", "watkins", "sidley", "kirkland", "winston", "strawn",
    "skadden", "weil gotshal", "paul weiss", "simpson thacher",
})

# Houston Form D items most likely to be venture-scale offerings
_VENTURE_ITEM_TAGS: dict[str, str] = {
    "01": "equity",
    "02": "debt",
    "03": "equity_and_debt",
    "04": "pooled_investment",
    "06B": "reg_d_506b",
    "06C": "reg_d_506c",
    "06": "reg_d",
}


def _parse_entity_name(display_names: list) -> str | None:
    """Extract company name from EDGAR display_names list.

    Format: ["COMPANY NAME  (TICKER)  (CIK XXXXXXXXXX)"]
    Returns: "COMPANY NAME" (title-cased, stripped).
    """
    if not display_names:
        return None
    raw = display_names[0] if isinstance(display_names, list) else display_names
    # Strip trailing (TICKER) and (CIK ...) suffixes
    name = re.sub(r"\s*\([^)]*\)\s*$", "", str(raw)).strip()
    # Remove multiple internal spaces
    return re.sub(r"\s{2,}", " ", name) or None


def _is_law_firm(name: str | None) -> bool:
    """True if entity name contains a known Houston law firm keyword."""
    if not name:
        return False
    name_lower = name.lower()
    return any(kw in name_lower for kw in _LAW_FIRM_KEYWORDS)


def _build_filing_url(cik: str | None, adsh: str | None) -> str | None:
    """Build EDGAR filing index URL from CIK and accession number."""
    if not cik or not adsh:
        return None
    adsh_nodash = adsh.replace("-", "")
    cik_int = cik.lstrip("0")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{adsh_nodash}/"
    )


class SecEdgarFormDHarvester(BaseHarvester):
    """Harvest Form D filings from SEC EDGAR for Houston-based issuers.

    Rolling 12-month window by default. Paginator fetches up to 1,000 hits.
    Each hit produces one RawCompanyRecord. Company name parsed from
    display_names; law firm filings flagged in extra.
    """

    SOURCE_NAME: ClassVar[str] = "SEC EDGAR Form D"
    SOURCE_URL: ClassVar[str] = _EFTS_URL
    SOURCE_TYPE: ClassVar[str] = "government_filing"
    UPDATE_CADENCE: ClassVar[str] = "realtime"
    SCRAPE_METHOD: ClassVar[str] = "rest_api"
    AUTH_REQUIRED: ClassVar[bool] = False
    EXPECTED_YIELD: ClassVar[str] = "80-120"

    def __init__(
        self,
        rate_limiter=None,
        lookback_days: int = 395,
    ) -> None:
        super().__init__(rate_limiter=rate_limiter)
        self._lookback_days = lookback_days

    def fetch(self) -> list[RawCompanyRecord]:
        """Fetch rolling-window Form D filings for Houston, TX issuers.

        Returns one RawCompanyRecord per hit. Law-firm-filed records are
        included but flagged. Empty list on HTTP error.
        """
        end_date = date.today()
        start_date = end_date - timedelta(days=self._lookback_days)

        records: list[RawCompanyRecord] = []
        from_offset = 0

        while True:
            self.rate_limiter.wait()
            hits = self._fetch_page(start_date, end_date, from_offset)
            if hits is None:
                break
            for hit in hits:
                rec = self._to_record(hit)
                if rec:
                    records.append(rec)
            if len(hits) < _PAGE_SIZE:
                break
            from_offset += _PAGE_SIZE
            if from_offset >= 1000:  # EDGAR EFTS hard cap
                logger.warning(
                    "[sec_edgar:cap] Reached 1000-hit EDGAR cap; "
                    "remaining filings not retrieved"
                )
                break

        logger.info(f"[sec_edgar:done] {len(records)} records extracted")
        return records

    def _fetch_page(
        self, start: date, end: date, from_offset: int
    ) -> list | None:
        """Fetch a single page of EDGAR EFTS results.

        Returns list of hits (may be empty), or None on error.
        """
        params = {
            "q": '"Houston, TX"',
            "forms": "D",
            "dateRange": "custom",
            "startdt": start.isoformat(),
            "enddt": end.isoformat(),
            "from": from_offset,
            "hits.hits": _PAGE_SIZE,
        }
        try:
            resp = requests.get(
                _EFTS_URL, params=params, headers=_HEADERS, timeout=20
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error(f"[sec_edgar:fetch-error] {exc}")
            raise

        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])
        total = data.get("hits", {}).get("total", {}).get("value", 0)
        if from_offset == 0:
            logger.info(f"[sec_edgar:total] {total} Form D hits for Houston, TX")
        return hits

    @staticmethod
    def _to_record(hit: dict) -> RawCompanyRecord | None:
        """Convert a single EFTS hit to a RawCompanyRecord."""
        src = hit.get("_source", {})

        display_names = src.get("display_names", [])
        name = _parse_entity_name(display_names)
        if not name:
            return None

        adsh = src.get("adsh")
        ciks = src.get("ciks", [])
        cik = ciks[0] if ciks else None
        file_date = src.get("file_date")
        items = src.get("items", [])

        source_url = _build_filing_url(cik, adsh)
        tags = [
            _VENTURE_ITEM_TAGS[item]
            for item in items
            if item in _VENTURE_ITEM_TAGS
        ]

        law_firm_filed = _is_law_firm(name)
        if law_firm_filed:
            logger.debug(
                f"[sec_edgar:law-firm] '{name}' flagged as law firm filer"
            )

        return RawCompanyRecord(
            name=name,
            source="SEC EDGAR Form D",
            source_url=source_url,
            description=None,
            website=None,
            location_raw="Houston, TX",
            tags=tags,
            extra={
                "adsh": adsh,
                "cik": cik,
                "file_date": file_date,
                "form_d_items": items,
                "form_d_filed_by_law_firm": law_firm_filed,
            },
        )
