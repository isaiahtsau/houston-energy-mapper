"""
Tests for llm/client.py.

Tests are grouped into:
  1. Cost estimation (dry_run=True) — no API key required
  2. Circuit breaker behavior
  3. Prompt loading errors (missing file → FileNotFoundError)
  4. Response parsing (structured output via Pydantic)
  5. _split_prompt helper

API call tests (marked with @pytest.mark.requires_api_key) are skipped in CI
unless ANTHROPIC_API_KEY is present in the environment.
"""
from __future__ import annotations

import os
import pytest
from pathlib import Path
from pydantic import BaseModel

from llm.client import (
    LLMCircuitBreakerError,
    LLMResponse,
    _split_prompt,
    _calculate_cost,
    _parse_structured_response,
    estimate_cost,
    reset_call_count,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_circuit_breaker():
    """Reset LLM call counter before each test to isolate circuit breaker state."""
    reset_call_count()
    yield
    reset_call_count()


@pytest.fixture
def dummy_prompt(tmp_path, monkeypatch):
    """Create a temporary prompt file and patch PROMPTS_DIR to point to it."""
    import llm.prompt_loader as pl

    prompt_dir = tmp_path / "prompts"
    prompt_dir.mkdir()
    prompt_file = prompt_dir / "test_v1.md"
    prompt_file.write_text("Classify: {{ company_name }}\n---USER---\nDescribe: {{ company_name }}")

    monkeypatch.setattr(pl, "PROMPTS_DIR", prompt_dir)
    return prompt_dir


# ─────────────────────────────────────────────────────────────────────────────
# _split_prompt
# ─────────────────────────────────────────────────────────────────────────────

def test_split_prompt_with_delimiter():
    rendered = "System instructions\n---USER---\nUser message"
    system, user = _split_prompt(rendered)
    assert system == "System instructions"
    assert user == "User message"


def test_split_prompt_without_delimiter():
    rendered = "Just a user message"
    system, user = _split_prompt(rendered)
    assert system == ""
    assert user == "Just a user message"


# ─────────────────────────────────────────────────────────────────────────────
# Cost calculation
# ─────────────────────────────────────────────────────────────────────────────

def test_calculate_cost_sonnet():
    # 1M input + 1M output at Sonnet pricing = $3.00 + $15.00
    cost = _calculate_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
    assert abs(cost - 18.00) < 0.01


def test_calculate_cost_unknown_model_falls_back_to_sonnet():
    cost_unknown = _calculate_cost("unknown-model-xyz", 1000, 500)
    cost_sonnet = _calculate_cost("claude-sonnet-4-6", 1000, 500)
    assert cost_unknown == cost_sonnet


# ─────────────────────────────────────────────────────────────────────────────
# Structured output parsing
# ─────────────────────────────────────────────────────────────────────────────

class _SampleSchema(BaseModel):
    score: float
    label: str


def test_parse_structured_response_valid():
    content = '{"score": 0.85, "label": "HIGH"}'
    result = _parse_structured_response(content, _SampleSchema, "test-call-id", "test")
    assert result is not None
    assert result.score == 0.85
    assert result.label == "HIGH"


def test_parse_structured_response_invalid_json():
    result = _parse_structured_response("not json", _SampleSchema, "test-call-id", "test")
    assert result is None


def test_parse_structured_response_wrong_schema():
    # Valid JSON but missing required field
    result = _parse_structured_response('{"score": 0.5}', _SampleSchema, "test-call-id", "test")
    assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# Dry run (no API key needed)
# ─────────────────────────────────────────────────────────────────────────────

def test_dry_run_returns_cost_estimate(dummy_prompt):
    from llm.client import call_llm
    from config.settings import settings

    # Ensure no real API call is made by using dry_run=True
    response = call_llm(
        prompt_name="test",
        prompt_version="v1",
        variables={"company_name": "Cemvita"},
        dry_run=True,
        auto_inject_examples=False,
    )
    assert isinstance(response, LLMResponse)
    assert response.content == ""
    assert response.input_tokens > 0
    assert response.cost_usd > 0
    assert response.latency_ms == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Circuit breaker
# ─────────────────────────────────────────────────────────────────────────────

def test_circuit_breaker_raises_after_limit(dummy_prompt, monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "max_llm_calls", 0)

    from llm.client import call_llm
    with pytest.raises(LLMCircuitBreakerError):
        call_llm(
            prompt_name="test",
            prompt_version="v1",
            variables={"company_name": "Test"},
            dry_run=True,
            auto_inject_examples=False,
        )
