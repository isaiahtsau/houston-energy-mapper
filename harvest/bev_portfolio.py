"""
Breakthrough Energy Ventures (BEV) portfolio harvester.

Harvests company records from https://www.breakthroughenergy.org/portfolio.
The page is a Nuxt 3 SSR application. All company data is embedded in a
window.__INITIAL_STATE__ Pinia store JSON blob (≈656 KB) in the HTML.
No JavaScript execution required.

Access pattern (per live-site inspection 2026-05-02):
  - URL: https://www.breakthroughenergy.org/portfolio (200 OK, Akamai CDN cache)
  - https://breakthroughenergy.com/investing/bev → Akamai 403 — NOT used
  - Chrome UA required to avoid 403.

Company record location inside __INITIAL_STATE__:
  Any nested dict where system.type == "company". Found via recursive walk.

Per-company schema:
  {
    "elements": {
      "title":        {"value": "<name>"},
      "description":  {"value": "<HTML description>"},
      "url":          {"value": "<website>"},
      "tags":         {"value": [{"name": "...", "codename": "..."}]},
      "technologies": {"value": [{"name": "...", "codename": "..."}]}
    },
    "system": {"codename": "<bev_codename>", "type": "company", "id": "<uuid>"}
  }

Description HTML is stripped to plain text. Tags are built from both sector
tags and technology names (deduplicated). Extra stores bev_codename + bev_id
for downstream cross-referencing with the BEF fellows roster.

Expected yield: 180-220 records (~206 companies at build time).
"""
from __future__ import annotations

import json
import logging
import re
from html.parser import HTMLParser
from typing import ClassVar

import requests

from harvest.base import BaseHarvester, RawCompanyRecord

logger = logging.getLogger(__name__)

_SOURCE_URL = "https://www.breakthroughenergy.org/portfolio"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_STATE_MARKER = "window.__INITIAL_STATE__"


# ── HTML stripper ──────────────────────────────────────────────────────────────


class _HTMLStripper(HTMLParser):
    """Minimal HTML → plain-text converter."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    @property
    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self._parts)).strip()


def _strip_html(raw: str) -> str:
    if not raw:
        return ""
    stripper = _HTMLStripper()
    stripper.feed(raw)
    return stripper.text


# ── JSON extraction ────────────────────────────────────────────────────────────


def _extract_state_json(html: str) -> dict | None:
    """Locate and parse window.__INITIAL_STATE__ from the HTML page.

    Uses json.JSONDecoder.raw_decode() starting at the opening brace of the
    assignment, which handles arbitrary JSON size without regex truncation.
    """
    idx = html.find(_STATE_MARKER)
    if idx == -1:
        logger.warning(
            "[bev:no-state] window.__INITIAL_STATE__ not found in HTML — "
            "page structure may have changed"
        )
        return None

    brace_idx = html.find("{", idx + len(_STATE_MARKER))
    if brace_idx == -1:
        logger.warning("[bev:no-brace] No opening brace after __INITIAL_STATE__")
        return None

    decoder = json.JSONDecoder()
    try:
        obj, _ = decoder.raw_decode(html, brace_idx)
        return obj
    except json.JSONDecodeError as exc:
        logger.warning(f"[bev:json-error] Failed to parse __INITIAL_STATE__: {exc}")
        return None


# ── Recursive company finder ───────────────────────────────────────────────────


def _find_companies(obj: object) -> list[dict]:
    """Recursively walk *obj* and return all dicts where system.type == 'company'."""
    results: list[dict] = []

    if isinstance(obj, dict):
        system = obj.get("system")
        if isinstance(system, dict) and system.get("type") == "company":
            results.append(obj)
        else:
            for value in obj.values():
                results.extend(_find_companies(value))
    elif isinstance(obj, list):
        for item in obj:
            results.extend(_find_companies(item))

    return results


# ── Harvester ──────────────────────────────────────────────────────────────────


class BevPortfolioHarvester(BaseHarvester):
    """Harvest company records from the BEV portfolio page.

    Single HTTP GET to the .org/portfolio page; parse window.__INITIAL_STATE__
    via recursive walk for system.type == 'company'. One RawCompanyRecord per
    unique company. Description HTML stripped to plain text.
    """

    SOURCE_NAME: ClassVar[str] = "Breakthrough Energy Ventures"
    SOURCE_URL: ClassVar[str] = _SOURCE_URL
    SOURCE_TYPE: ClassVar[str] = "vc_portfolio"
    UPDATE_CADENCE: ClassVar[str] = "quarterly"
    SCRAPE_METHOD: ClassVar[str] = "static_ssr"
    AUTH_REQUIRED: ClassVar[bool] = False
    EXPECTED_YIELD: ClassVar[str] = "180-220"

    def fetch(self) -> list[RawCompanyRecord]:
        """Fetch the portfolio page and parse all company records.

        Returns one RawCompanyRecord per company object found in
        window.__INITIAL_STATE__. Empty list on HTTP error or parse failure.
        """
        self.rate_limiter.wait()
        try:
            resp = requests.get(_SOURCE_URL, headers=_HEADERS, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error(f"[bev:fetch-error] {exc}")
            raise

        state = _extract_state_json(resp.text)
        if state is None:
            logger.error("[bev:no-state] Could not extract __INITIAL_STATE__; returning []")
            return []

        company_objs = _find_companies(state)
        logger.info(f"[bev:found] {len(company_objs)} company objects in __INITIAL_STATE__")

        records: list[RawCompanyRecord] = []
        for obj in company_objs:
            rec = self._to_record(obj)
            if rec is not None:
                records.append(rec)

        logger.info(f"[bev:done] {len(records)} records extracted")
        return records

    @staticmethod
    def _to_record(company: dict) -> RawCompanyRecord | None:
        """Convert a raw company object to a RawCompanyRecord.

        Returns None if the company has no name.
        """
        elements = company.get("elements") or {}
        system = company.get("system") or {}

        name_raw = ((elements.get("title") or {}).get("value") or "").strip()
        if not name_raw:
            return None

        desc_raw = (elements.get("description") or {}).get("value") or ""
        description = _strip_html(desc_raw) or None

        website_raw = ((elements.get("url") or {}).get("value") or "").strip()
        website = website_raw or None

        # Tags: sector tags first, then technology names (deduplicated, order-preserving)
        tags: list[str] = []
        seen: set[str] = set()
        for tag_obj in (elements.get("tags") or {}).get("value") or []:
            tag_name = (tag_obj.get("name") or "").strip()
            if tag_name and tag_name not in seen:
                tags.append(tag_name)
                seen.add(tag_name)
        for tech_obj in (elements.get("technologies") or {}).get("value") or []:
            tech_name = (tech_obj.get("name") or "").strip()
            if tech_name and tech_name not in seen:
                tags.append(tech_name)
                seen.add(tech_name)

        return RawCompanyRecord(
            name=name_raw,
            source="Breakthrough Energy Ventures",
            source_url=_SOURCE_URL,
            description=description,
            website=website,
            location_raw=None,
            tags=tags,
            extra={
                "bev_codename": system.get("codename"),
                "bev_id": system.get("id"),
            },
        )
