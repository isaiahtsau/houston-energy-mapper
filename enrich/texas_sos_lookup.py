"""
Texas SOS / Comptroller franchise tax enrichment lookup.

Per-company query to the Texas Comptroller franchise tax search API.
Used during enrichment to determine whether a company is a Texas entity
with a Houston-area mailing address — signal `texas_sos_houston_county_formation`.

Access pattern (per live-site inspection 2026-05-02):
  URL: https://mycpa.cpa.state.tx.us/coa/Index.html redirects to
       https://comptroller.texas.gov/data-search/franchise-tax
  API: GET https://comptroller.texas.gov/data-search/franchise-tax?name={name}
  Auth: None.
  Rate limit: no documented limit; treat as polite (1 req/s in enrichment pipeline).

Response JSON:
  {
    "success": true,
    "data": [
      {
        "name": "ENTITY NAME",
        "taxpayerId": "32100693152",
        "mailingAddressZip": "77002"     # or "CANADA", "N/A", etc.
      }
    ],
    "count": 1
  }

Error case (too many results):
  {
    "success": false,
    "error": "Search will return 901 entries. Please refine search."
  }

The `mailingAddressZip` field is a raw string — it may be a 5-digit ZIP,
a foreign country name, "N/A", or None. `is_houston_area` is True only
for recognized Houston-county 5-digit ZIP codes.

Houston area ZIP codes: Harris County (770xx) plus key suburban ZIPs in
Fort Bend (774xx), Montgomery (773xx), Brazoria (775xx), Galveston (775xx),
Waller (774xx) counties.

Public API: lookup_texas_sos(company_name) -> TexasSosResult
"""
from __future__ import annotations

import logging
import re
import urllib.parse
from typing import TypedDict

import requests

logger = logging.getLogger(__name__)

_TX_SOS_URL = "https://comptroller.texas.gov/data-search/franchise-tax"

_HEADERS = {
    "User-Agent": "IonTakeHome research@ion.com",
    "Accept": "application/json",
}

# Houston-area ZIP code prefixes (first 3 digits → Harris + major suburb counties)
# Harris County: 770xx (full range)
# Fort Bend: 77401, 77406, 77407, 77417, 77430, 77441, 77450, 77461, 77469, 77477, 77478, 77479, 77489, 77494, 77498
# Montgomery: 77301-77389
# Brazoria: 77511-77583
# Galveston: 77550-77599
# This uses a set of common 3-digit prefixes; false positives are tolerable at this stage.
_HOUSTON_ZIP3_PREFIXES: frozenset[str] = frozenset({
    "770",  # Harris County core
    "773",  # Montgomery County / Conroe
    "774",  # Fort Bend / Katy / Sugar Land
    "775",  # Brazoria / Galveston / Clear Lake
    "776",  # Galveston Island
    "777",  # South Houston / Pearland / League City
})


def _is_houston_zip(zip_val: str | None) -> bool:
    """True if zip_val is a recognized Houston-area 5-digit ZIP code."""
    if not zip_val:
        return False
    cleaned = str(zip_val).strip()
    if not re.match(r"^\d{5}$", cleaned):
        return False
    return cleaned[:3] in _HOUSTON_ZIP3_PREFIXES


class TexasSosResult(TypedDict):
    found: bool
    is_houston_area: bool          # True if any result has a Houston-area ZIP
    matched_name: str | None       # canonical name from Comptroller DB
    taxpayer_id: str | None        # first matching taxpayer ID
    mailing_zip: str | None        # mailing address ZIP from Comptroller
    result_count: int              # raw count returned by API
    raw_results: list[dict]        # full list of result dicts from API


def lookup_texas_sos(company_name: str) -> TexasSosResult:
    """Look up a company in the Texas Comptroller franchise tax database.

    Args:
        company_name: Company name as it appears in pipeline data. The
            Comptroller search is a prefix/substring match.

    Returns:
        TexasSosResult dict. `found` is True if the API returned ≥1 result.
        `is_houston_area` is True if any result's ZIP is in Houston area.
    """
    if not company_name or not company_name.strip():
        return _empty_result()

    name = company_name.strip()
    params = {"name": name}
    url = _TX_SOS_URL + "?" + urllib.parse.urlencode(params)

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning(f"[texas_sos:request-error] {company_name!r}: {exc}")
        return _empty_result()

    try:
        data = resp.json()
    except ValueError as exc:
        logger.warning(f"[texas_sos:json-error] {company_name!r}: {exc}")
        return _empty_result()

    if not data.get("success"):
        error_msg = data.get("error", "")
        logger.debug(f"[texas_sos:api-error] {company_name!r}: {error_msg}")
        return _empty_result()

    results: list[dict] = data.get("data", [])
    count: int = data.get("count", len(results))

    if not results:
        return TexasSosResult(
            found=False,
            is_houston_area=False,
            matched_name=None,
            taxpayer_id=None,
            mailing_zip=None,
            result_count=0,
            raw_results=[],
        )

    # Take the first result as the primary match; check all for Houston ZIP
    first = results[0]
    matched_name = first.get("name")
    taxpayer_id = first.get("taxpayerId")
    mailing_zip = first.get("mailingAddressZip")

    is_houston = any(_is_houston_zip(r.get("mailingAddressZip")) for r in results)

    logger.debug(
        f"[texas_sos:found] {company_name!r} → {matched_name!r} "
        f"zip={mailing_zip!r} houston={is_houston}"
    )

    return TexasSosResult(
        found=True,
        is_houston_area=is_houston,
        matched_name=matched_name,
        taxpayer_id=taxpayer_id,
        mailing_zip=str(mailing_zip) if mailing_zip is not None else None,
        result_count=count,
        raw_results=results,
    )


def _empty_result() -> TexasSosResult:
    return TexasSosResult(
        found=False,
        is_houston_area=False,
        matched_name=None,
        taxpayer_id=None,
        mailing_zip=None,
        result_count=0,
        raw_results=[],
    )
