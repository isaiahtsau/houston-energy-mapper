"""
Abstract base class for all source harvesters.

Every harvester in the harvest/ package subclasses BaseHarvester and must:
  1. Set all ClassVar source metadata fields (SOURCE_NAME, SOURCE_URL, etc.)
  2. Implement fetch() → list[RawCompanyRecord]
  3. Set requires_browser = True if fetch() uses Playwright

The base class provides:
  - run() public entry point with timing, structured logging, and error isolation
  - Graceful degradation: a failed harvester logs and returns empty records;
    it never crashes the pipeline
  - Yield validation: warns if a source returns fewer or far more records than expected

Source metadata convention (required on every subclass):
  SOURCE_NAME:     Human-readable name, e.g. "Rice Alliance Clean Energy Accelerator"
  SOURCE_URL:      Canonical URL of the source
  SOURCE_TYPE:     One of: vc_portfolio | accelerator | government_filing | rss |
                            government_api | job_feed | patent_db | trade_press
  UPDATE_CADENCE:  One of: daily | weekly | monthly | quarterly | annual | on_demand
  SCRAPE_METHOD:   One of: static | headless | api | rss | pdf
  AUTH_REQUIRED:   True if the source requires login or an API key
  EXPECTED_YIELD:  Rough string range, e.g. "20-50" (used for anomaly detection)
"""
from __future__ import annotations

import datetime
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Normalized record emitted by every harvester
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RawCompanyRecord:
    """Source-agnostic, normalized record produced by every harvester.

    All fields except name and source are optional — downstream stages
    (classifier, enricher, presence scorer) tolerate missing fields gracefully
    and record gaps as confidence-lowering signals.

    Design note: this dataclass is intentionally flat and serialization-friendly.
    Downstream stages write it to SQLite via storage/db.py.

    Fields:
        name:         Company name as it appears in the source (not normalized).
        source:       BaseHarvester.SOURCE_NAME of the originating harvester.
        source_url:   URL of the specific page or document where this record was found.
        description:  Raw text description from the source (may be marketing copy).
        website:      Company website URL if present in the source.
        location_raw: Location string as it appears in the source ("Houston, TX").
        tags:         Technology or sector tags listed by the source.
        extra:        Source-specific fields that don't map to the normalized schema.
                      Downstream stages may inspect extra for additional signals.
        harvested_at: UTC timestamp of harvest (set automatically on construction).
    """
    name: str
    source: str
    source_url: str | None = None
    description: str | None = None
    website: str | None = None
    location_raw: str | None = None
    tags: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    harvested_at: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Harvest result container
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class HarvestResult:
    """Outcome of a single harvester run, including audit metadata.

    The orchestrator collects HarvestResult objects from every harvester and
    writes them to the run log. success=False does not crash the pipeline —
    it just means this source contributed zero records to this run.
    """
    source_name: str
    records: list[RawCompanyRecord]
    success: bool
    error: str | None = None            # populated when success=False
    duration_seconds: float = 0.0
    started_at: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


# ─────────────────────────────────────────────────────────────────────────────
# Abstract base class
# ─────────────────────────────────────────────────────────────────────────────

class BaseHarvester(ABC):
    """Abstract base class that every source harvester must subclass.

    Subclassing checklist:
      1. Define all ClassVar metadata fields listed in the module docstring.
      2. Implement fetch() to return list[RawCompanyRecord].
      3. Set requires_browser = True if fetch() needs Playwright.
      4. Call self.rate_limiter.wait() between outbound HTTP requests in fetch().
      5. Do NOT override run() — put all scraping logic in fetch().

    The orchestrator calls run(), not fetch() directly. run() wraps fetch() with
    timing, logging, and error isolation. A harvester that raises in fetch() gets
    success=False in its HarvestResult; the pipeline continues with other sources.

    Thread safety: each harvester instance is not thread-safe. The orchestrator
    runs harvesters sequentially (or in a controlled concurrent pool). Do not
    share instances across threads.
    """

    # ── Required class-level source metadata (override in every subclass) ─────
    SOURCE_NAME: ClassVar[str]
    SOURCE_URL: ClassVar[str]
    SOURCE_TYPE: ClassVar[str]      # see module docstring for allowed values
    UPDATE_CADENCE: ClassVar[str]   # see module docstring for allowed values
    SCRAPE_METHOD: ClassVar[str]    # see module docstring for allowed values
    AUTH_REQUIRED: ClassVar[bool] = False
    EXPECTED_YIELD: ClassVar[str] = "unknown"   # e.g. "20-50"; "unknown" skips yield check

    # Set True in subclasses that need a Playwright Page object
    requires_browser: ClassVar[bool] = False

    def __init__(
        self,
        rate_limiter=None,      # utils.rate_limiter.RateLimiter | None
        browser_page=None,      # playwright.sync_api.Page | None; typed loosely to avoid hard dep
    ) -> None:
        """Initialize the harvester with shared infrastructure.

        Args:
            rate_limiter:  Shared RateLimiter from the orchestrator. If None,
                           a per-harvester instance is created using settings defaults.
                           Pass a shared instance to coordinate delays across concurrent
                           requests to the same domain.
            browser_page:  Playwright Page object. Required when requires_browser=True.
                           The orchestrator owns the browser lifecycle and passes this in;
                           harvesters should not create their own browsers.
        """
        # Local import avoids a circular dependency at module load time
        from utils.rate_limiter import RateLimiter
        from config.settings import settings

        self.rate_limiter = rate_limiter or RateLimiter(
            min_delay_seconds=settings.scrape_delay_seconds
        )
        self.browser_page = browser_page

        if self.requires_browser and self.browser_page is None:
            logger.warning(
                f"{self.__class__.__name__} declares requires_browser=True but "
                "no browser_page was provided. fetch() will likely fail. "
                "The orchestrator should pass browser_page= when instantiating this harvester."
            )

    @abstractmethod
    def fetch(self) -> list[RawCompanyRecord]:
        """Fetch and normalize raw company records from this source.

        Implementations MUST:
          - Return list[RawCompanyRecord] (may be empty; never raise on empty).
          - Call self.rate_limiter.wait() between outbound HTTP requests.
          - Not crash on partial failures: yield what is available, log what was skipped.
          - Not classify, enrich, or score data — leave that to downstream stages.
          - Not deduplicate — the dedupe stage handles cross-source merging.

        Returns:
            List of RawCompanyRecord instances. Empty list is valid.
        """
        ...

    def run(self) -> HarvestResult:
        """Public entry point for the orchestrator.

        Wraps fetch() with timing, structured logging, error isolation,
        and yield validation. Do not override this method.

        Returns:
            HarvestResult with records and audit metadata.
            success=False if fetch() raised any exception.
        """
        logger.info(f"[harvest:start] {self.SOURCE_NAME} ({self.SCRAPE_METHOD})")
        started_at = datetime.datetime.now(datetime.timezone.utc)
        t0 = time.monotonic()

        try:
            records = self.fetch()
            duration = time.monotonic() - t0
            self._check_yield(records)
            logger.info(
                f"[harvest:ok] {self.SOURCE_NAME} — "
                f"{len(records)} records in {duration:.1f}s"
            )
            return HarvestResult(
                source_name=self.SOURCE_NAME,
                records=records,
                success=True,
                duration_seconds=duration,
                started_at=started_at,
            )

        except Exception as exc:
            duration = time.monotonic() - t0
            logger.error(
                f"[harvest:fail] {self.SOURCE_NAME} after {duration:.1f}s — {exc}",
                exc_info=True,
            )
            return HarvestResult(
                source_name=self.SOURCE_NAME,
                records=[],
                success=False,
                error=str(exc),
                duration_seconds=duration,
                started_at=started_at,
            )

    def _check_yield(self, records: list[RawCompanyRecord]) -> None:
        """Warn if record count is outside the expected range (EXPECTED_YIELD).

        This catches two common failure modes silently:
          - A source returned far fewer records than expected (site structure changed)
          - A harvester returned 2x+ records (possible duplicate pagination)

        Does nothing if EXPECTED_YIELD is "unknown" or malformed.
        """
        if self.EXPECTED_YIELD == "unknown":
            return
        try:
            parts = self.EXPECTED_YIELD.split("-")
            low, high = int(parts[0]), int(parts[-1])
            count = len(records)
            if count < low:
                logger.warning(
                    f"[harvest:yield-low] {self.SOURCE_NAME}: got {count} records, "
                    f"expected ≥{low}. Source structure may have changed."
                )
            elif count > high * 2:
                logger.warning(
                    f"[harvest:yield-high] {self.SOURCE_NAME}: got {count} records, "
                    f"expected ≤{high}. Possible duplicate pagination or structure change."
                )
        except (ValueError, IndexError):
            pass  # malformed EXPECTED_YIELD string — skip silently
