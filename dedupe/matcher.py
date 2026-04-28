"""
Deduplication: fuzzy name matching and canonical ID promotion.

The dedup stage runs after all harvesters have written to the database and
merges company records that refer to the same entity across sources.

Two merge strategies:
  1. Domain match (exact):  Two records with the same canonical_domain
     are merged immediately.
  2. Name similarity (fuzzy): Records where normalize_name(a) and normalize_name(b)
     score ≥ settings.dedup_name_similarity_threshold (default 88) via rapidfuzz
     token_sort_ratio are flagged as likely duplicates. Scores ≥ 95 are
     auto-merged; scores 88–94 go to the manual review queue.

Canonical ID promotion:
  When a provisional-ID record (slugify(name)) is enriched with a canonical_domain,
  the ID is promoted to slugify(domain). All FK references (raw_records.company_id,
  llm_call_log.run_id) are updated atomically.

Status: STUB — interface defined, implementation in Step 10.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass
class DedupeMatch:
    """A candidate duplicate pair identified by the matcher."""
    id_a: str
    id_b: str
    name_a: str
    name_b: str
    similarity_score: float     # rapidfuzz token_sort_ratio, 0–100
    auto_merge: bool            # True if score ≥ 95 (auto-merged); False = review queue


def run_dedup(conn: sqlite3.Connection) -> list[DedupeMatch]:
    """Find and merge duplicate company records.

    Args:
        conn: Open pipeline.db connection.

    Returns:
        List of DedupeMatch records (auto-merged and review-queued).

    Note:
        STUB — raises NotImplementedError until Step 10.
    """
    raise NotImplementedError("run_dedup — implemented in Step 10")


def promote_canonical_id(
    conn: sqlite3.Connection,
    provisional_id: str,
    canonical_domain: str,
) -> str:
    """Promote a provisional (name-based) ID to a canonical (domain-based) ID.

    Args:
        conn:             Open pipeline.db connection.
        provisional_id:   Current provisional slug (from company name).
        canonical_domain: Resolved domain (e.g. "cemvita.com").

    Returns:
        The new canonical ID string.

    Note:
        STUB — raises NotImplementedError until Step 10.
    """
    raise NotImplementedError("promote_canonical_id — implemented in Step 10")
