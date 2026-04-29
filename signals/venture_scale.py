"""
Venture-scale classification — v2 per docs/venture_scale_rubric.md.

Two-pass system per company:
  1. apply_hard_exclude_rules — deterministic Python checks; no LLM call.
     If a rule matches, the company is excluded with a structured reason.
  2. classify_venture_scale — LLM classifier via prompts/classifier_v1.md.
     Stubbed until Step 6; raises NotImplementedError.

CompanyRecord and shared constants are imported from models.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from models import CORPORATE_VC_WHITELIST, HOUSTON_MAJORS, CompanyRecord

# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------


@dataclass
class HardExcludeResult:
    """Result of apply_hard_exclude_rules()."""

    excluded: bool
    rule_id: str | None = None   # e.g. "HX-01"
    reason: str | None = None    # human-readable explanation


class VentureScaleClassification(BaseModel):
    """Structured output of the LLM venture-scale classifier (Step 6)."""

    company_id: str
    score: float                                            # 0.0–10.0
    tier: Literal["VENTURE_SCALE", "BORDERLINE", "NOT_VENTURE_SCALE"]
    confidence: Literal["HIGH", "MEDIUM", "LOW"]
    positive_signals: list[str]    # signal IDs from the rubric
    false_positive_patterns: list[str]  # pattern IDs from the rubric
    reasoning: str                 # 2–4 sentences referencing specific evidence
    review_queue: bool             # True if BORDERLINE or LOW confidence


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

PF_ROUND_TYPES: frozenset[str] = frozenset({
    "PF-Debt", "Project Finance Debt", "Project Bond",
})

SERVICES_DESC_KEYWORDS: frozenset[str] = frozenset({
    "consulting", "advisory", "managed services",
    "engineering services", "professional services",
})

SERVICES_PRIMARY_BUSINESS: frozenset[str] = frozenset({
    "consulting", "services", "advisory",
})

SPV_DESC_KEYWORDS: frozenset[str] = frozenset({
    "special purpose vehicle", "single purpose entity",
})

IP_LICENSING_KEYWORDS: frozenset[str] = frozenset({
    "ip licensing", "patent licensing", "patent monetization",
})

# ---------------------------------------------------------------------------
# Rule HX-01: PF-debt-only round
# ---------------------------------------------------------------------------


def _check_hx01(company: CompanyRecord) -> HardExcludeResult:
    """Triggers if most_recent_round.round_type is a project-finance type."""
    if not company.most_recent_round:
        return HardExcludeResult(excluded=False)
    round_type = company.most_recent_round.get("round_type", "")
    if round_type in PF_ROUND_TYPES:
        return HardExcludeResult(
            excluded=True,
            rule_id="HX-01",
            reason=(
                f"PF-Debt round excluded: round type '{round_type}' is project finance debt,"
                " not venture equity. Different return profile, investor universe, and risk structure."
            ),
        )
    return HardExcludeResult(excluded=False)


# ---------------------------------------------------------------------------
# Rule HX-02: Pure services revenue with no IP
# ---------------------------------------------------------------------------


def _has_services_language(company: CompanyRecord) -> bool:
    """True if description contains services keywords OR primary_business is a services category."""
    desc_lower = company.description.lower()
    if any(kw in desc_lower for kw in SERVICES_DESC_KEYWORDS):
        return True
    if company.primary_business and company.primary_business.lower() in SERVICES_PRIMARY_BUSINESS:
        return True
    return False


def _has_cvc_investor(company: CompanyRecord) -> bool:
    """True if any investor substring-matches a Corporate VC whitelist entry."""
    return any(
        cvc.lower() in investor.lower()
        for cvc in CORPORATE_VC_WHITELIST
        for investor in company.investors
    )


def _check_hx02(company: CompanyRecord) -> HardExcludeResult:
    """Triggers if: services language AND no patents AND no federal grants AND no CVC."""
    if not _has_services_language(company):
        return HardExcludeResult(excluded=False)
    if company.patents or company.federal_grants or _has_cvc_investor(company):
        return HardExcludeResult(excluded=False)
    return HardExcludeResult(
        excluded=True,
        rule_id="HX-02",
        reason=(
            "Pure services company excluded: services revenue without IP, federal grants,"
            " or corporate VC investment. No defensible non-services thesis to evaluate."
        ),
    )


# ---------------------------------------------------------------------------
# Rule HX-03: Single-asset project SPV
# ---------------------------------------------------------------------------


def _has_entity_suffix(name: str) -> bool:
    return any(suffix in name for suffix in ("LLC", "LP", "Holdings"))


def _description_is_spv(description: str) -> bool:
    """True if description contains explicit SPV language."""
    desc_lower = description.lower()
    if any(kw in desc_lower for kw in SPV_DESC_KEYWORDS):
        return True
    # Standalone word "spv" (word-boundary check)
    if re.search(r"\bspv\b", desc_lower):
        return True
    return False


def _check_hx03(company: CompanyRecord) -> HardExcludeResult:
    """Safe harbor: technology_vendor_identity set → not excluded.
    Triggers if: entity suffix AND SPV description AND (DOE project participant OR Form D single asset).
    """
    # Safe harbor: distinct technology vendor identity present
    if company.technology_vendor_identity:
        return HardExcludeResult(excluded=False)

    if not _has_entity_suffix(company.name):
        return HardExcludeResult(excluded=False)

    if not _description_is_spv(company.description):
        return HardExcludeResult(excluded=False)

    # Condition (a): DOE OCED hub project participant
    hub_participant = (
        company.doe_oced_hub is not None
        and company.doe_oced_hub.get("role", "").lower() == "project participant"
    )
    # Condition (b): Form D names a specific asset as use-of-proceeds
    form_d_single_asset = (
        company.form_d is not None
        and bool(company.form_d.get("use_of_proceeds"))
    )

    if hub_participant or form_d_single_asset:
        return HardExcludeResult(
            excluded=True,
            rule_id="HX-03",
            reason=(
                "SPV excluded: single-asset project vehicle without standalone technology"
                " vendor identity. The technology provider is the venture-scale entity; the SPV is not."
            ),
        )
    return HardExcludeResult(excluded=False)


# ---------------------------------------------------------------------------
# Rule HX-04: Patent-troll structure
# ---------------------------------------------------------------------------


def _check_hx04(company: CompanyRecord) -> HardExcludeResult:
    """Triggers if: <5 employees AND IP-licensing business model AND no products AND no customers."""
    employee_count = company.employee_count
    if employee_count is None or employee_count >= 5:
        return HardExcludeResult(excluded=False)

    biz_model = (company.business_model or "").lower()
    if not any(kw in biz_model for kw in IP_LICENSING_KEYWORDS):
        return HardExcludeResult(excluded=False)

    # None and [] are treated identically (absence convention)
    if company.products or company.customers:
        return HardExcludeResult(excluded=False)

    return HardExcludeResult(
        excluded=True,
        rule_id="HX-04",
        reason=(
            "Patent-troll structure excluded: IP-licensing entity with <5 employees,"
            " no products, and no customers. Not a venture-scale operating company."
        ),
    )


# ---------------------------------------------------------------------------
# Rule HX-05: Wholly-owned major subsidiary
# ---------------------------------------------------------------------------


def _check_hx05(company: CompanyRecord) -> HardExcludeResult:
    """Triggers if parent_organization is a Houston major AND is_subsidiary is True."""
    parent = company.parent_organization
    if not parent:
        return HardExcludeResult(excluded=False)

    in_majors = parent in HOUSTON_MAJORS or any(
        parent.lower() in m.lower() or m.lower() in parent.lower()
        for m in HOUSTON_MAJORS
    )
    if not in_majors:
        return HardExcludeResult(excluded=False)

    if company.is_subsidiary:
        return HardExcludeResult(
            excluded=True,
            rule_id="HX-05",
            reason=(
                f"Major subsidiary excluded: {company.name} is a wholly-owned subsidiary"
                f" of {parent}. Capture the parent's technology partners separately."
            ),
        )
    return HardExcludeResult(excluded=False)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def apply_hard_exclude_rules(company: CompanyRecord) -> HardExcludeResult:
    """Run all deterministic hard-exclude checks in order; return on first match.

    Pure function — no I/O, no LLM calls, no side effects.
    If no rule matches, returns HardExcludeResult(excluded=False).
    """
    for check_fn in (_check_hx01, _check_hx02, _check_hx03, _check_hx04, _check_hx05):
        result = check_fn(company)
        if result.excluded:
            return result
    return HardExcludeResult(excluded=False)


def classify_venture_scale(
    company: CompanyRecord,
    examples_bank: list[dict] | None = None,
) -> VentureScaleClassification:
    """LLM-judged venture-scale classification via the classifier prompt.

    Not yet implemented — requires prompts/classifier_v1.md, which is drafted
    in Step 6 after the first harvester run produces real records to calibrate against.

    Args:
        company:       CompanyRecord that passed apply_hard_exclude_rules.
        examples_bank: Few-shot examples from data/validated_examples.jsonl.
                       Auto-injected by the pipeline orchestrator.

    Raises:
        NotImplementedError: Always — implemented in Step 6.
    """
    raise NotImplementedError(
        "classify_venture_scale — implemented in Step 6 after prompts/classifier_v1.md is drafted"
    )
