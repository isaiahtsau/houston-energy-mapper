"""
LLM API wrapper for the Houston Energy Mapper pipeline.

This module is the single, auditable entry point for all Anthropic API calls.
No other module in the pipeline calls the Anthropic SDK directly.

Every call through this module is:
  - Loaded from a versioned prompt file (inline strings are a hard error)
  - Optionally validated against a Pydantic schema (triggers JSON mode)
  - Retried with exponential backoff on transient API errors (tenacity)
  - Counted against the circuit breaker cap (settings.max_llm_calls)
  - Logged with prompt name, version, model, token counts, estimated cost, and latency
  - Enriched with validated few-shot examples from the flywheel bank (auto_inject_examples)

Public interface:
    call_llm(prompt_name, prompt_version, variables, ...) → LLMResponse
    estimate_cost(prompt_name, prompt_version, variables, ...) → dict

Design decisions:
  - call_llm() is a function, not a class method. The Anthropic client is a
    module-level singleton (lazy-initialized). This keeps call sites simple.
  - temperature=0.0 by default. Classifiers must be deterministic; enrichers
    can pass temperature=0.3 for more natural summary prose if desired.
  - JSON mode is activated automatically when response_schema is provided.
    A JSON instruction is appended to the system prompt rather than using a
    separate API parameter, for compatibility across model versions.
  - auto_inject_examples=True is the flywheel hook: as validated_examples.jsonl
    grows, every classifier call silently gets smarter without orchestrator changes.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import anthropic
from pydantic import BaseModel
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from llm.prompt_loader import load_prompt

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Pricing table
# ─────────────────────────────────────────────────────────────────────────────
# USD per 1 million tokens. Update when Anthropic changes rates.
# Source: https://www.anthropic.com/pricing
_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6":  {"input": 3.00,  "output": 15.00},
    "claude-opus-4-6":    {"input": 15.00, "output": 75.00},
    "claude-haiku-4-5":   {"input": 0.25,  "output": 1.25},
}


# ─────────────────────────────────────────────────────────────────────────────
# Response container
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LLMResponse:
    """Container for a single LLM API call result, including full audit metadata.

    Every field here is written to the structured log line. The call_id correlates
    the LLMResponse to a specific log entry, API request, and database record.

    Attributes:
        content:          Raw text returned by the model.
        parsed:           Populated when response_schema is provided and parsing succeeds.
                          None on parse failure (logged as warning; call still succeeds).
        prompt_name:      Name of the prompt template used (e.g. "classifier").
        prompt_version:   Version string used (e.g. "v1").
        model:            Anthropic model identifier as returned in the API response.
        input_tokens:     Tokens consumed by the prompt (from API usage object).
        output_tokens:    Tokens in the completion (from API usage object).
        cost_usd:         Estimated cost from _PRICING table. Not billed exactly.
        latency_ms:       Wall-clock milliseconds from request send to first response.
        call_id:          UUID4 string. Appears in logs, DB records, and run reports.
    """
    content: str
    parsed: BaseModel | None
    prompt_name: str
    prompt_version: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    call_id: str


# ─────────────────────────────────────────────────────────────────────────────
# Custom exceptions
# ─────────────────────────────────────────────────────────────────────────────

class LLMCallError(Exception):
    """Raised when an LLM API call fails after all retries are exhausted.

    Includes the call_id, prompt name/version, model, and original error
    in the message for easy log correlation.
    """


class LLMCircuitBreakerError(LLMCallError):
    """Raised when the pipeline has hit settings.max_llm_calls.

    Use --max-llm-calls 0 on the CLI to disable, or increase the limit.
    """


# ─────────────────────────────────────────────────────────────────────────────
# Anthropic client singleton
# ─────────────────────────────────────────────────────────────────────────────

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    """Return a lazily-initialized, module-level Anthropic client singleton.

    Lazy initialization means the API key is only read when the first LLM call
    is made, not at module import time. This allows the pipeline to import
    llm.client in a dry-run context without raising KeyError.
    """
    global _client
    if _client is None:
        from config.settings import settings
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client


# ─────────────────────────────────────────────────────────────────────────────
# Internal: retry predicate
# ─────────────────────────────────────────────────────────────────────────────

def _is_retryable(exc: BaseException) -> bool:
    """Return True for transient API errors that warrant an automatic retry.

    Retried: RateLimitError, APIConnectionError, 5xx APIStatusError.
    Not retried: 4xx APIStatusError (bad request, auth failure — programming errors).
    """
    if isinstance(exc, (anthropic.RateLimitError, anthropic.APIConnectionError)):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code >= 500
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Internal: raw API call (separated so tenacity wraps a clean function)
# ─────────────────────────────────────────────────────────────────────────────

def _call_api_raw(
    client: anthropic.Anthropic,
    model: str,
    max_tokens: int,
    temperature: float,
    system_prompt: str,
    user_message: str,
) -> anthropic.types.Message:
    """Issue a single Anthropic messages.create call.

    This function is the sole point where the SDK is called. It is intentionally
    thin — all retry logic, JSON mode injection, and logging live above this layer.
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "user", "content": user_message}],
    }
    if system_prompt:
        kwargs["system"] = system_prompt
    return client.messages.create(**kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# Internal: cost calculation
# ─────────────────────────────────────────────────────────────────────────────

def _calculate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate the USD cost of a completed API call.

    Falls back to Sonnet pricing for unknown model identifiers so that cost
    estimation never crashes the pipeline — it may just be inaccurate.
    """
    pricing = _PRICING.get(model, _PRICING["claude-sonnet-4-6"])
    return (
        input_tokens * pricing["input"] + output_tokens * pricing["output"]
    ) / 1_000_000


# ─────────────────────────────────────────────────────────────────────────────
# Internal: split rendered prompt into (system, user) turns
# ─────────────────────────────────────────────────────────────────────────────

def _split_prompt(rendered: str) -> tuple[str, str]:
    """Split a rendered prompt string into (system_prompt, user_message) tuple.

    Convention: prompt files may contain a line "---USER---" as a delimiter.
    Everything before the delimiter is the system prompt (sets model behavior
    and few-shot context); everything after is the user turn (the actual task).
    If no delimiter is found, the entire rendered string becomes the user message.

    Returns:
        (system_prompt, user_message) — either may be empty string, never None.
    """
    delimiter = "---USER---"
    if delimiter in rendered:
        parts = rendered.split(delimiter, maxsplit=1)
        return parts[0].strip(), parts[1].strip()
    return "", rendered.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Internal: JSON response parser
# ─────────────────────────────────────────────────────────────────────────────

def _parse_structured_response(
    content: str,
    schema: type[BaseModel],
    call_id: str,
    prompt_name: str,
) -> BaseModel | None:
    """Attempt to parse a JSON model response into a Pydantic model instance.

    Returns None on failure (with a warning log) rather than raising, so that
    one malformed API response does not abort the pipeline batch. The caller
    (and the run log) records parse failures for manual review.

    Args:
        content:     Raw string from the model (should be JSON).
        schema:      Pydantic model class to validate against.
        call_id:     UUID for log correlation.
        prompt_name: Prompt name for log context.

    Returns:
        Populated Pydantic model instance, or None on any parse/validation error.
    """
    # Strip markdown code fences if model wrapped the JSON despite instructions.
    stripped = content.strip()
    if stripped.startswith("```"):
        # Remove opening fence (```json or ```) and closing ```
        stripped = stripped.split("\n", 1)[-1]  # drop first line (``` or ```json)
        if stripped.endswith("```"):
            stripped = stripped[: stripped.rfind("```")]
        stripped = stripped.strip()
    else:
        stripped = content

    try:
        data = json.loads(stripped)
        return schema.model_validate(data)
    except json.JSONDecodeError as exc:
        logger.warning(
            f"[llm:parse-fail] Model returned non-JSON response "
            f"[call_id={call_id}, prompt={prompt_name}]: {exc}\n"
            f"Raw content (first 200 chars): {content[:200]}"
        )
    except Exception as exc:
        logger.warning(
            f"[llm:parse-fail] Pydantic validation failed for {schema.__name__} "
            f"[call_id={call_id}, prompt={prompt_name}]: {exc}"
        )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Internal: flywheel example loader (lazy import for scaffolding compatibility)
# ─────────────────────────────────────────────────────────────────────────────

def _load_flywheel_examples(prompt_name: str) -> list[dict]:
    """Load validated few-shot examples keyed by prompt_name from the flywheel bank.

    The flywheel module (flywheel/examples_bank.py) is imported lazily here for
    two reasons:
      1. During scaffolding, examples_bank.py is a stub — lazy import prevents
         ImportError if the module doesn't exist yet.
      2. It avoids a hard module-level dependency that would load all flywheel
         state (including SQLite connections) just by importing llm.client.

    Once flywheel/examples_bank.py is fully implemented (Step 11), this function
    returns real, curated few-shot examples that compound with each pipeline run.

    Returns:
        List of example dicts (may be empty). Each dict has "input", "output", "note".
    """
    try:
        from flywheel.examples_bank import load_examples_for_prompt
        return load_examples_for_prompt(prompt_name)
    except ImportError:
        logger.debug(
            "flywheel.examples_bank not yet implemented — skipping auto-injection"
        )
        return []
    except Exception as exc:
        logger.warning(
            f"[llm:flywheel] Failed to load examples for '{prompt_name}': {exc}. "
            "Continuing without few-shot injection."
        )
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Circuit breaker state
# ─────────────────────────────────────────────────────────────────────────────

_llm_call_count: int = 0


def get_call_count() -> int:
    """Return the number of LLM API calls made in the current process lifetime.

    Used by the orchestrator to report total calls in the run log.
    """
    return _llm_call_count


def reset_call_count() -> None:
    """Reset the call counter. Used in tests to isolate circuit breaker state."""
    global _llm_call_count
    _llm_call_count = 0


# ─────────────────────────────────────────────────────────────────────────────
# Public interface: call_llm
# ─────────────────────────────────────────────────────────────────────────────

def call_llm(
    prompt_name: str,
    prompt_version: str,
    variables: dict[str, Any],
    response_schema: type[BaseModel] | None = None,
    model: str | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.0,
    few_shot_examples: list[dict] | None = None,
    auto_inject_examples: bool = True,
    dry_run: bool = False,
) -> LLMResponse:
    """Execute a single, fully-auditable LLM API call.

    This is the only function in the pipeline that calls the Anthropic SDK.
    All classifiers, enrichers, and scorers call this function.

    Args:
        prompt_name:          Base name of the prompt file, e.g. "classifier".
                              Combined with prompt_version to resolve the file path.
        prompt_version:       Version string, e.g. "v1". Changing a prompt requires
                              saving a new version file (classifier_v2.md) — never
                              edit an existing version in place.
        variables:            Jinja2 template variables substituted into the prompt.
                              All {{ keys }} in the prompt file must be present here.
        response_schema:      Optional Pydantic model class. When provided, a JSON
                              instruction is appended to the system prompt and the
                              response is parsed and validated. LLMResponse.parsed
                              is populated on success, None on parse failure.
        model:                Anthropic model ID. Defaults to settings.classifier_model
                              (claude-sonnet-4-6). Only pass settings.qa_model
                              (claude-opus-4-6) for borderline QA passes.
        max_tokens:           Maximum completion tokens. Increase for enrichment
                              tasks that generate longer summaries.
        temperature:          0.0 for deterministic classification (default).
                              0.3 is acceptable for summary generation prose.
        few_shot_examples:    Explicit few-shot examples. If provided, these override
                              any auto-injected flywheel examples for this call.
        auto_inject_examples: When True (default) and few_shot_examples is None,
                              load relevant examples from the validated examples bank
                              keyed by prompt_name. This is the flywheel: every
                              classifier call gets smarter as validations accumulate.
        dry_run:              When True, render the prompt and estimate cost without
                              making an API call. Returns a placeholder LLMResponse
                              with content="" and estimated token counts.

    Returns:
        LLMResponse with content, optional parsed model, token counts, cost, and latency.

    Raises:
        FileNotFoundError:         Prompt file not found — programming error, fix the path.
        LLMCircuitBreakerError:    settings.max_llm_calls limit reached.
        LLMCallError:              API call failed after all retries (wrapped original exc).
    """
    global _llm_call_count
    from config.settings import settings

    # ── Circuit breaker ────────────────────────────────────────────────────────
    if settings.max_llm_calls is not None and _llm_call_count >= settings.max_llm_calls:
        raise LLMCircuitBreakerError(
            f"max_llm_calls limit of {settings.max_llm_calls} reached after "
            f"{_llm_call_count} calls. Use --max-llm-calls to adjust."
        )

    effective_model = model or settings.classifier_model
    call_id = str(uuid.uuid4())

    # ── Resolve few-shot examples ──────────────────────────────────────────────
    # Priority: explicit argument > auto-injected flywheel > none
    resolved_examples: list[dict] | None = few_shot_examples
    if resolved_examples is None and auto_inject_examples:
        resolved_examples = _load_flywheel_examples(prompt_name) or None

    # ── Render the prompt ──────────────────────────────────────────────────────
    rendered = load_prompt(
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        variables=variables,
        few_shot_examples=resolved_examples,
    )
    system_prompt, user_message = _split_prompt(rendered)

    # Append JSON instruction to system prompt when structured output is requested
    if response_schema is not None:
        json_instruction = (
            "\n\nYou MUST respond with a valid JSON object only. "
            "No prose, no markdown fences, no explanation before or after the JSON."
        )
        system_prompt = (system_prompt + json_instruction).strip()

    # ── Dry run: estimate cost without API call ────────────────────────────────
    if dry_run:
        # Rough token estimate: 1 token ≈ 4 characters
        estimated_input = len(system_prompt + user_message) // 4
        estimated_output = max_tokens // 3  # conservative: assume ~1/3 of max used
        return LLMResponse(
            content="",
            parsed=None,
            prompt_name=prompt_name,
            prompt_version=prompt_version,
            model=effective_model,
            input_tokens=estimated_input,
            output_tokens=estimated_output,
            cost_usd=_calculate_cost(effective_model, estimated_input, estimated_output),
            latency_ms=0.0,
            call_id=call_id,
        )

    # ── API call with retry ────────────────────────────────────────────────────
    client = _get_client()

    # Build a retrying wrapper around _call_api_raw with current settings
    # (built here rather than as a decorator so settings.llm_max_retries is
    # evaluated at call time, not at module import time)
    retrying_call = retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        stop=stop_after_attempt(settings.llm_max_retries),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )(_call_api_raw)

    t0 = time.monotonic()
    try:
        message = retrying_call(
            client=client,
            model=effective_model,
            max_tokens=max_tokens,
            temperature=temperature,
            system_prompt=system_prompt,
            user_message=user_message,
        )
    except (
        anthropic.RateLimitError,
        anthropic.APIConnectionError,
        anthropic.APIStatusError,
    ) as exc:
        raise LLMCallError(
            f"LLM call failed after {settings.llm_max_retries} retries "
            f"[call_id={call_id}, prompt={prompt_name}/{prompt_version}, "
            f"model={effective_model}]: {exc}"
        ) from exc

    latency_ms = (time.monotonic() - t0) * 1000
    _llm_call_count += 1

    raw_content = message.content[0].text if message.content else ""
    input_tokens = message.usage.input_tokens
    output_tokens = message.usage.output_tokens
    cost = _calculate_cost(effective_model, input_tokens, output_tokens)

    # ── Parse structured output ────────────────────────────────────────────────
    parsed: BaseModel | None = None
    if response_schema is not None:
        parsed = _parse_structured_response(raw_content, response_schema, call_id, prompt_name)

    # ── Structured log line ────────────────────────────────────────────────────
    logger.info(
        "[llm:ok] %(prompt_name)s/%(prompt_version)s | %(model)s | "
        "in=%(input_tokens)d out=%(output_tokens)d | $%(cost_usd).5f | %(latency_ms).0fms | "
        "parsed=%(parsed_ok)s | id=%(call_id)s",
        {
            "prompt_name": prompt_name,
            "prompt_version": prompt_version,
            "model": effective_model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 5),
            "latency_ms": round(latency_ms, 1),
            "parsed_ok": parsed is not None or response_schema is None,
            "call_id": call_id,
        },
    )

    return LLMResponse(
        content=raw_content,
        parsed=parsed,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        model=effective_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
        latency_ms=latency_ms,
        call_id=call_id,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public interface: estimate_cost
# ─────────────────────────────────────────────────────────────────────────────

def estimate_cost(
    prompt_name: str,
    prompt_version: str,
    variables: dict[str, Any],
    model: str | None = None,
    max_tokens: int = 2048,
    few_shot_examples: list[dict] | None = None,
    auto_inject_examples: bool = True,
) -> dict[str, float]:
    """Estimate the cost of an LLM call without making an API request.

    Used by the orchestrator's --dry-run mode to surface cost projections
    before any API calls are made. Also useful for the CLI's `hem status` command.

    Args:
        (same as call_llm, minus response_schema and dry_run)

    Returns:
        Dict with keys:
          - "input_tokens":     Estimated input token count.
          - "output_tokens_est": Estimated output token count (max_tokens / 3).
          - "cost_usd_est":     Estimated cost in USD.
    """
    response = call_llm(
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        variables=variables,
        model=model,
        max_tokens=max_tokens,
        few_shot_examples=few_shot_examples,
        auto_inject_examples=auto_inject_examples,
        dry_run=True,
    )
    return {
        "input_tokens": response.input_tokens,
        "output_tokens_est": response.output_tokens,
        "cost_usd_est": response.cost_usd,
    }
