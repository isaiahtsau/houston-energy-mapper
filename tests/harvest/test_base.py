"""
Tests for harvest/base.py — BaseHarvester ABC and RawCompanyRecord.

These tests do not require any external HTTP calls or API keys.
They verify the contract of the base class using a minimal concrete subclass.
"""
from __future__ import annotations

import datetime
import pytest
from unittest.mock import MagicMock

from harvest.base import BaseHarvester, HarvestResult, RawCompanyRecord


# ─────────────────────────────────────────────────────────────────────────────
# Minimal concrete harvester for testing
# ─────────────────────────────────────────────────────────────────────────────

class _SuccessHarvester(BaseHarvester):
    """Harvester that always returns two dummy records."""
    SOURCE_NAME = "Test Source"
    SOURCE_URL = "https://example.com"
    SOURCE_TYPE = "test"
    UPDATE_CADENCE = "on_demand"
    SCRAPE_METHOD = "static"
    EXPECTED_YIELD = "1-5"

    def fetch(self) -> list[RawCompanyRecord]:
        return [
            RawCompanyRecord(name="Company A", source=self.SOURCE_NAME),
            RawCompanyRecord(name="Company B", source=self.SOURCE_NAME, website="https://b.com"),
        ]


class _FailingHarvester(BaseHarvester):
    """Harvester that always raises in fetch()."""
    SOURCE_NAME = "Failing Source"
    SOURCE_URL = "https://example.com/fail"
    SOURCE_TYPE = "test"
    UPDATE_CADENCE = "on_demand"
    SCRAPE_METHOD = "static"

    def fetch(self) -> list[RawCompanyRecord]:
        raise RuntimeError("Simulated scrape failure")


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_successful_harvester_returns_records():
    """run() should return a HarvestResult with success=True and the expected records."""
    harvester = _SuccessHarvester()
    result = harvester.run()

    assert isinstance(result, HarvestResult)
    assert result.success is True
    assert len(result.records) == 2
    assert result.error is None
    assert result.duration_seconds >= 0.0


def test_failing_harvester_does_not_raise():
    """run() should catch fetch() exceptions and return success=False, not propagate."""
    harvester = _FailingHarvester()
    result = harvester.run()

    assert isinstance(result, HarvestResult)
    assert result.success is False
    assert result.records == []
    assert "Simulated scrape failure" in result.error


def test_raw_company_record_defaults():
    """RawCompanyRecord should populate harvested_at automatically."""
    record = RawCompanyRecord(name="Test Co", source="Test Source")

    assert record.name == "Test Co"
    assert record.source == "Test Source"
    assert record.tags == []
    assert record.extra == {}
    assert isinstance(record.harvested_at, datetime.datetime)
    assert record.harvested_at.tzinfo is not None  # must be timezone-aware


def test_yield_warning_below_minimum(caplog):
    """A harvester returning fewer records than expected should log a warning."""
    import logging

    class _LowYieldHarvester(BaseHarvester):
        SOURCE_NAME = "Low Yield Source"
        SOURCE_URL = "https://example.com"
        SOURCE_TYPE = "test"
        UPDATE_CADENCE = "on_demand"
        SCRAPE_METHOD = "static"
        EXPECTED_YIELD = "10-20"

        def fetch(self) -> list[RawCompanyRecord]:
            return [RawCompanyRecord(name="Only One", source=self.SOURCE_NAME)]

    with caplog.at_level(logging.WARNING):
        harvester = _LowYieldHarvester()
        harvester.run()

    assert any("yield-low" in record.message or "below expected" in record.message
               for record in caplog.records)
