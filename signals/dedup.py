"""
Step 10 — Cross-source deduplication.

Identifies companies that appear multiple times in the pipeline under different
names or from different sources, and merges them into a single canonical record.

Algorithm (three passes, in priority order):
  1. Canonical domain match: normalize URL → bare domain (strip scheme/www/path).
     Two records with the same non-null domain are the same company. Match score: 1.0.
  2. Fuzzy name match: strip corporate suffixes and parentheticals, lowercase,
     then compute rapidfuzz.fuzz.token_sort_ratio. Threshold: ≥ 88.
     Only applied to records that were NOT already matched by domain.
  3. Union-Find grouping: match pairs form a graph; connected components determine
     merge groups so transitive matches (A=B, B=C → A=B=C) are handled correctly.

Canonical record selection (within each merge group):
  Priority: Rice ETVF > Greentown > ECV > Halliburton > Energytech Nexus >
            Lowercarbon > DCVC > RBPC > Ion District > GOOSE Capital >
            InnovationMap > ERCOT > SEC EDGAR > unknown.
  Tie-break: highest venture_scale_score; second tie: earliest first_seen_at.

Merge semantics:
  - source_ids: union of all JSON arrays across the group.
  - venture_scale_score, confidence, reasoning: from canonical record.
  - sub_sector, summary, founder_pedigree_*: from canonical record.
  - canonical_domain: first non-null across group.
  - Duplicate records: marked is_duplicate=1, canonical_id → canonical record id.

DB schema additions (added by _migrate_dedup_schema):
  companies.is_duplicate       INTEGER DEFAULT 0  -- 1 = merged away
  companies.canonical_id       TEXT               -- id of the canonical record
  companies.dedup_match_type   TEXT               -- 'domain' | 'fuzzy_name' | 'canonical'
  companies.enrichment_status  TEXT               -- 'enriched' | 'pending_description' | 'off_thesis'

Public API:
  run_dedup(conn) -> DedupResult
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import NamedTuple

logger = logging.getLogger(__name__)

# ── Source quality priority (lower index = higher priority) ────────────────────

_SOURCE_PRIORITY: list[str] = [
    "Rice Energy Tech Venture Forum (ETVF)",
    "Greentown Houston",
    "Energy Capital Ventures",
    "Halliburton Labs",
    "Energytech Nexus",
    "Lowercarbon Capital",
    "DCVC",
    "RBPC Alumni",
    "Ion District",
    "GOOSE Capital",
    "InnovationMap Houston RSS",
    "ERCOT Interconnection Queue",
    "SEC EDGAR Form D",
]

# ── Corporate suffix patterns (stripped during name normalization) ─────────────

# Note: functional words like "energy", "technologies", "systems", "solutions"
# are NOT stripped — they are semantically load-bearing in energy company names.
_SUFFIX_RE = re.compile(
    r"\b(inc|llc|lp|ltd|corp|co\.?|plc|gmbh|bv|pbc|llp|holdings|group|international)\b"
    r"[\.,]?\s*$",
    re.IGNORECASE,
)
_PARENS_RE = re.compile(r"\s*\([^)]*\)")

FUZZY_THRESHOLD = 88  # token_sort_ratio threshold for name match


# ── Result types ───────────────────────────────────────────────────────────────

class MergeCase(NamedTuple):
    canonical_id: str
    canonical_name: str
    duplicate_ids: list[str]        # all non-canonical ids in the group
    duplicate_names: list[str]
    match_type: str                 # 'domain' | 'fuzzy_name'
    canonical_source: str
    all_sources: list[str]


@dataclass
class DedupResult:
    total_before: int = 0
    total_after: int = 0             # unique canonical companies
    merges: int = 0                  # number of merge groups (each ≥1 duplicate)
    duplicates_removed: int = 0      # total records marked is_duplicate=1
    domain_matches: int = 0
    fuzzy_matches: int = 0
    enrichment_status_updated: int = 0
    merge_cases: list[MergeCase] = field(default_factory=list)


# ── Schema migration ───────────────────────────────────────────────────────────

def _migrate_dedup_schema(conn: sqlite3.Connection) -> None:
    """Add dedup columns to companies if they don't exist."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(companies)")}
    for col_def in [
        "is_duplicate       INTEGER DEFAULT 0",
        "canonical_id       TEXT",
        "dedup_match_type   TEXT",
        "enrichment_status  TEXT",
    ]:
        col_name = col_def.split()[0]
        if col_name not in existing:
            conn.execute(f"ALTER TABLE companies ADD COLUMN {col_def}")
    conn.commit()


# ── Normalization helpers ──────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    """Lowercase, strip parentheticals and corporate suffixes for fuzzy matching."""
    n = name.strip()
    n = _PARENS_RE.sub("", n)       # strip (web), (Inc), etc.
    for _ in range(3):              # iterative suffix stripping
        prev = n
        n = _SUFFIX_RE.sub("", n).strip().rstrip(",").strip()
        if n == prev:
            break
    return n.lower().strip()


def normalize_domain(url: str | None) -> str | None:
    """Strip scheme, www, and path from a URL to get the bare domain."""
    if not url or not url.strip():
        return None
    d = url.lower().strip().rstrip("/")
    d = re.sub(r"^https?://", "", d)
    d = re.sub(r"^www\.", "", d)
    d = d.split("/")[0].split("?")[0].split("#")[0]
    # Reject generic or placeholder domains
    if not d or "." not in d or d in {"example.com", "localhost"}:
        return None
    return d


# ── Union-Find ─────────────────────────────────────────────────────────────────

class UnionFind:
    """Path-compressed union-find for grouping company records."""

    def __init__(self, ids: list[str]) -> None:
        self._parent: dict[str, str] = {i: i for i in ids}

    def find(self, x: str) -> str:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]  # path compression
            x = self._parent[x]
        return x

    def union(self, a: str, b: str) -> bool:
        """Merge groups containing a and b. Returns True if they were separate."""
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self._parent[rb] = ra
        return True

    def groups(self) -> dict[str, list[str]]:
        """Return {root: [member_ids]} for groups with ≥2 members."""
        groups: dict[str, list[str]] = {}
        for node in self._parent:
            root = self.find(node)
            groups.setdefault(root, []).append(node)
        return {r: members for r, members in groups.items() if len(members) >= 2}


# ── Source priority scoring ────────────────────────────────────────────────────

def _source_priority(source_ids_json: str | None) -> int:
    """Return the best (lowest-index) priority for this record's sources."""
    if not source_ids_json:
        return len(_SOURCE_PRIORITY)
    try:
        sources: list[str] = json.loads(source_ids_json)
    except (json.JSONDecodeError, TypeError):
        return len(_SOURCE_PRIORITY)
    best = len(_SOURCE_PRIORITY)
    for src in sources:
        for i, canonical_src in enumerate(_SOURCE_PRIORITY):
            if canonical_src.lower() in src.lower() or src.lower() in canonical_src.lower():
                best = min(best, i)
                break
    return best


def _select_canonical(
    group_ids: list[str], rows: dict[str, dict]
) -> str:
    """Select the canonical record from a merge group.

    Priority: best source quality → highest vs_score → earliest first_seen_at.
    """
    def sort_key(cid: str) -> tuple:
        r = rows[cid]
        prio = _source_priority(r.get("source_ids"))
        score = -(r.get("venture_scale_score") or 0.0)  # negate: higher = better
        seen = r.get("first_seen_at") or "9999"
        return (prio, score, seen)

    return min(group_ids, key=sort_key)


# ── Enrichment status computation ──────────────────────────────────────────────

def compute_enrichment_status(row: dict) -> str:
    """Determine enrichment_status for a single company row.

    Values:
      'off_thesis'          — sub_sector = 'off_thesis'
      'pending_description' — score=5.0, confidence=LOW, no description available
      'enriched'            — has sub_sector and summary populated (and on-thesis)
    """
    sub_sector = row.get("sub_sector") or ""
    if sub_sector == "off_thesis":
        return "off_thesis"

    score = row.get("venture_scale_score")
    confidence = (row.get("venture_scale_confidence") or "").upper()
    summary = row.get("summary") or ""

    if score == 5.0 and confidence == "LOW" and not summary.strip():
        return "pending_description"

    return "enriched"


# ── Main dedup logic ───────────────────────────────────────────────────────────

def run_dedup(conn: sqlite3.Connection) -> DedupResult:
    """Run the full dedup pass on the companies table.

    Idempotent: re-running resets is_duplicate/canonical_id before re-computing.

    Returns:
        DedupResult with merge counts, match type breakdown, and MergeCase samples.
    """
    _migrate_dedup_schema(conn)

    # Reset any previous dedup state
    conn.execute("UPDATE companies SET is_duplicate=0, canonical_id=NULL, dedup_match_type=NULL")
    conn.commit()

    # Load all non-excluded companies
    rows_raw = conn.execute(
        """
        SELECT id, name, name_normalized, source_ids, canonical_domain,
               venture_scale_score, venture_scale_confidence, first_seen_at,
               sub_sector, summary
        FROM companies
        """
    ).fetchall()

    rows: dict[str, dict] = {}
    for r in rows_raw:
        d = dict(r)
        # Supplement canonical_domain from raw website column if needed
        if not d.get("canonical_domain"):
            d["canonical_domain"] = None
        rows[d["id"]] = d

    all_ids = list(rows.keys())
    result = DedupResult(total_before=len(all_ids))

    if not all_ids:
        result.total_after = 0
        return result

    uf = UnionFind(all_ids)

    # ── Pass 1: Domain match ───────────────────────────────────────────────────
    domain_to_ids: dict[str, list[str]] = {}
    for cid, row in rows.items():
        # Try canonical_domain first, then fall back to website from raw_records
        domain = normalize_domain(row.get("canonical_domain"))
        if domain is None:
            # Check raw_records for website
            rr = conn.execute(
                "SELECT website FROM raw_records WHERE name_raw = ? AND website IS NOT NULL LIMIT 1",
                (row["name"],),
            ).fetchone()
            if rr:
                domain = normalize_domain(rr["website"])
        if domain:
            domain_to_ids.setdefault(domain, []).append(cid)

    domain_match_pairs: set[tuple[str, str]] = set()
    for domain, ids_for_domain in domain_to_ids.items():
        if len(ids_for_domain) < 2:
            continue
        for i in range(len(ids_for_domain)):
            for j in range(i + 1, len(ids_for_domain)):
                a, b = ids_for_domain[i], ids_for_domain[j]
                if uf.union(a, b):
                    result.domain_matches += 1
                    domain_match_pairs.add((min(a, b), max(a, b)))

    # ── Pass 2: Fuzzy name match ───────────────────────────────────────────────
    # Only compare pairs not already matched by domain
    from rapidfuzz import fuzz

    # Build normalized name index
    norm_names: dict[str, str] = {cid: normalize_name(row["name"]) for cid, row in rows.items()}

    # For efficiency: group by first 3 chars of normalized name
    # (avoids O(n²) full comparison — only compare within same prefix bucket)
    prefix_buckets: dict[str, list[str]] = {}
    for cid, norm in norm_names.items():
        prefix = norm[:4] if len(norm) >= 4 else norm
        prefix_buckets.setdefault(prefix, []).append(cid)

    # Also check pairs across buckets that share exact first token
    first_token_buckets: dict[str, list[str]] = {}
    for cid, norm in norm_names.items():
        first_token = norm.split()[0] if norm.split() else norm
        if len(first_token) >= 4:  # skip very short tokens (avoid false matches)
            first_token_buckets.setdefault(first_token, []).append(cid)

    # Pre-build source sets for same-source guard
    # Fund series and numbered SPVs within the same source (e.g. SEC EDGAR Form D,
    # ERCOT Interconnection Queue) are legally distinct entities and must NOT merge
    # via fuzzy match. Cross-source fuzzy match is preserved for legitimate cases like
    # "Aeromine Technologies" (ETVF) ← "Aeromine Technologies, Inc." (Greentown).
    def _sources_of(cid: str) -> frozenset[str]:
        raw = rows[cid].get("source_ids")
        if not raw:
            return frozenset()
        try:
            return frozenset(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            return frozenset()

    checked: set[tuple[str, str]] = set()

    def _check_pair(a: str, b: str) -> bool:
        """Try fuzzy match for pair; union if above threshold. Returns True if merged."""
        key = (min(a, b), max(a, b))
        if key in checked:
            return False
        checked.add(key)
        if uf.find(a) == uf.find(b):  # already in same group
            return False
        # Same-source guard: skip fuzzy match if both records share a common source.
        # This prevents fund-series false positives (e.g. "CAZ Fund III, L.P." vs
        # "CAZ Fund II, L.P." — both SEC EDGAR Form D) from merging. Domain match
        # is still applied for same-source records; only fuzzy is guarded here.
        if _sources_of(a) & _sources_of(b):
            return False
        na, nb = norm_names[a], norm_names[b]
        if not na or not nb:
            return False
        score = fuzz.token_sort_ratio(na, nb)
        if score >= FUZZY_THRESHOLD:
            uf.union(a, b)
            result.fuzzy_matches += 1
            return True
        return False

    for bucket_ids in list(prefix_buckets.values()) + list(first_token_buckets.values()):
        for i in range(len(bucket_ids)):
            for j in range(i + 1, len(bucket_ids)):
                _check_pair(bucket_ids[i], bucket_ids[j])

    # ── Build merge groups and write results ───────────────────────────────────
    groups = uf.groups()
    now_iso = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()

    for root, members in groups.items():
        # Determine canonical record
        canonical_id = _select_canonical(members, rows)

        # Collect all sources across the group
        all_sources: list[str] = []
        for mid in members:
            src_raw = rows[mid].get("source_ids")
            if src_raw:
                try:
                    all_sources.extend(json.loads(src_raw))
                except (json.JSONDecodeError, TypeError):
                    pass
        all_sources = list(dict.fromkeys(all_sources))  # dedup preserving order

        # Determine match type for this group
        group_match_type = "domain" if any(
            (min(a, b), max(a, b)) in domain_match_pairs
            for a in members for b in members if a != b
        ) else "fuzzy_name"

        # Update canonical record
        conn.execute(
            """
            UPDATE companies
               SET source_ids=?, dedup_match_type=?, last_updated_at=?
             WHERE id=?
            """,
            (
                json.dumps(all_sources, ensure_ascii=False),
                group_match_type,
                now_iso,
                canonical_id,
            ),
        )

        # Mark duplicates
        duplicates = [mid for mid in members if mid != canonical_id]
        for dup_id in duplicates:
            conn.execute(
                """
                UPDATE companies
                   SET is_duplicate=1, canonical_id=?, dedup_match_type=?,
                       last_updated_at=?
                 WHERE id=?
                """,
                (canonical_id, group_match_type, now_iso, dup_id),
            )

        # Record merge case (sample — store all)
        result.merge_cases.append(
            MergeCase(
                canonical_id=canonical_id,
                canonical_name=rows[canonical_id]["name"],
                duplicate_ids=duplicates,
                duplicate_names=[rows[d]["name"] for d in duplicates],
                match_type=group_match_type,
                canonical_source=json.loads(rows[canonical_id].get("source_ids") or '["?"]')[0],
                all_sources=all_sources,
            )
        )

        result.merges += 1
        result.duplicates_removed += len(duplicates)

    conn.commit()

    # ── Enrichment status pass ─────────────────────────────────────────────────
    # Apply to all non-duplicate records
    all_rows = conn.execute(
        "SELECT id, sub_sector, summary, venture_scale_score, venture_scale_confidence "
        "FROM companies WHERE is_duplicate=0"
    ).fetchall()

    for row in all_rows:
        status = compute_enrichment_status(dict(row))
        conn.execute(
            "UPDATE companies SET enrichment_status=? WHERE id=?",
            (status, row["id"]),
        )
        result.enrichment_status_updated += 1
    conn.commit()

    result.total_after = result.total_before - result.duplicates_removed

    logger.info(
        f"[dedup] {result.total_before} → {result.total_after} companies "
        f"({result.merges} merge groups, {result.duplicates_removed} duplicates removed, "
        f"domain={result.domain_matches} fuzzy={result.fuzzy_matches})"
    )
    return result
