"""
Central configuration for the Houston Energy Mapper pipeline.

All tunable parameters live here. Nothing is hard-coded in individual modules.
Change behavior by editing this file or by passing CLI flags — not by editing
harvester or signal code.

Usage:
    from config.settings import settings
    print(settings.classifier_model)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env before anything reads os.environ
load_dotenv()

# Absolute path to the project root (parent of config/)
_BASE_DIR = Path(__file__).parent.parent


@dataclass
class Settings:
    """All runtime-configurable parameters for the pipeline.

    Attribute groups:
      - API credentials
      - Model selection
      - Filesystem paths
      - Rate limiting and scrape politeness
      - LLM circuit breakers
      - Venture-scale classifier thresholds
      - Houston presence scorer thresholds
    """

    # ── API credentials ───────────────────────────────────────────────────────
    # Loaded from .env / environment. Will raise KeyError at import time if missing.
    anthropic_api_key: str = field(
        default_factory=lambda: os.environ["ANTHROPIC_API_KEY"]
    )

    # ── Model selection ───────────────────────────────────────────────────────
    # classifier_model and enricher_model default to Sonnet (fast, cost-effective).
    # qa_model is reserved for borderline-case QA passes — uses Opus sparingly.
    classifier_model: str = "claude-sonnet-4-6"
    enricher_model: str = "claude-sonnet-4-6"
    presence_model: str = "claude-sonnet-4-6"
    qa_model: str = "claude-opus-4-6"

    # ── Filesystem paths ──────────────────────────────────────────────────────
    base_dir: Path = field(default_factory=lambda: _BASE_DIR)
    prompts_dir: Path = field(default_factory=lambda: _BASE_DIR / "prompts")
    data_dir: Path = field(default_factory=lambda: _BASE_DIR / "data")
    db_dir: Path = field(default_factory=lambda: _BASE_DIR / "data" / "db")
    exports_dir: Path = field(default_factory=lambda: _BASE_DIR / "data" / "exports")
    validated_examples_path: Path = field(
        default_factory=lambda: _BASE_DIR / "data" / "validated_examples.jsonl"
    )
    corporate_vc_sources_path: Path = field(
        default_factory=lambda: _BASE_DIR / "config" / "corporate_vc_sources.yaml"
    )

    # ── Rate limiting ─────────────────────────────────────────────────────────
    # Minimum seconds between outbound HTTP requests per harvester.
    # 1.5s is polite for most sites; increase for sites with aggressive bot detection.
    scrape_delay_seconds: float = 1.5

    # ── LLM retry policy ─────────────────────────────────────────────────────
    # tenacity will retry up to this many times on transient API errors.
    llm_max_retries: int = 5

    # ── LLM circuit breaker ───────────────────────────────────────────────────
    # Hard cap on total LLM API calls per pipeline run. None = unlimited.
    # Set via CLI --max-llm-calls to prevent runaway cost on large candidate sets.
    max_llm_calls: int | None = None

    # ── Venture-scale classifier thresholds ───────────────────────────────────
    # Companies scoring above venture_scale_high_threshold pass to enrichment.
    # Companies scoring below venture_scale_low_threshold are hard-excluded.
    # Companies in between go to the manual review queue.
    venture_scale_high_threshold: float = 0.70
    venture_scale_low_threshold: float = 0.35

    # ── Houston presence scorer ───────────────────────────────────────────────
    # Signal point values (HIGH/MEDIUM/LOW) and tier cutoffs.
    # Defined here so the scorer and tests share the same constants.
    houston_high_signal_points: int = 3
    houston_medium_signal_points: int = 2
    houston_low_signal_points: int = 1

    # Tier thresholds (inclusive lower bounds)
    houston_tier_a_min_points: int = 6    # + ≥1 HIGH operational signal required
    houston_tier_b_min_points: int = 3
    houston_tier_b_low_min_points: int = 1
    # 0 points → Tier C (no credible Houston signal)

    # ── Deduplication ─────────────────────────────────────────────────────────
    # Minimum rapidfuzz ratio to consider two company names a match (0–100).
    dedup_name_similarity_threshold: int = 88


# Module-level singleton — import and use directly:
#   from config.settings import settings
settings = Settings()
