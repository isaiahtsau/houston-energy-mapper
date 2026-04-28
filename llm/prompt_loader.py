"""
Prompt template loader for the Houston Energy Mapper pipeline.

Prompts live in prompts/{name}_{version}.md (e.g. prompts/classifier_v1.md).
Every prompt file has an optional YAML-style header block (between --- markers)
describing the prompt's purpose, input/output shape, and version history.
The loader strips the header, renders Jinja2 template variables, and optionally
prepends few-shot examples from the validated examples bank.

Prompt file format:
    ---
    name: classifier
    version: v1
    purpose: Score a company against the venture-scale rubric
    input_shape: { name, description, website, tags, extra_signals }
    output_shape: { venture_scale_score, confidence, reasoning, hard_excluded, exclude_reason }
    changed_from_previous: n/a (initial version)
    ---

    [prompt body using {{ variable }} Jinja2 placeholders]

    ---USER---

    [optional user-turn section; everything above ---USER--- becomes the system prompt]

Design decisions:
  - StrictUndefined: missing template variables raise an error at render time rather
    than silently rendering as empty strings. This prevents malformed prompts from
    reaching the API.
  - The ---USER--- delimiter splits a prompt into (system, user) turns. Single-turn
    prompts (no delimiter) are treated as user-turn-only with an empty system prompt.
  - Few-shot examples are prepended as a structured Markdown block before the user
    message, so the model sees examples in context before the actual task.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined, TemplateNotFound

# Prompt files live adjacent to the project root
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

# Delimiter that splits system prompt from user message within a single file
_USER_DELIMITER = "---USER---"

# Regex that matches the YAML front-matter block (--- ... ---)
# Non-greedy, dot-all so it spans multiple lines
_HEADER_RE = re.compile(r"^---\n.*?\n---\n?", re.DOTALL)


def load_prompt(
    prompt_name: str,
    prompt_version: str,
    variables: dict[str, Any],
    few_shot_examples: list[dict] | None = None,
) -> str:
    """Load, render, and return a prompt string ready to be split and sent to the API.

    Steps:
      1. Resolve the prompt file path (raises FileNotFoundError if missing).
      2. Strip the YAML front-matter header.
      3. Render Jinja2 template variables (raises UndefinedError if a variable is missing).
      4. Prepend formatted few-shot examples if provided.

    Args:
        prompt_name:       Base name, e.g. "classifier". Combined with prompt_version
                           to form "classifier_v1.md".
        prompt_version:    Version string, e.g. "v1".
        variables:         Jinja2 template variables. All {{ keys }} in the prompt body
                           must be present here (StrictUndefined enforced).
        few_shot_examples: Optional list of example dicts to inject before the main
                           prompt body. Each dict must have "input" and "output" keys.
                           These come from flywheel/examples_bank.py or the caller.

    Returns:
        Rendered prompt string. May contain a ---USER--- delimiter — call
        llm.client._split_prompt() to separate system and user turns.

    Raises:
        FileNotFoundError:      Prompt file does not exist.
        jinja2.UndefinedError:  A required template variable is missing from variables.
    """
    prompt_path = get_prompt_path(prompt_name, prompt_version)
    raw = prompt_path.read_text(encoding="utf-8")

    # Strip YAML front-matter, if present
    body = _strip_header(raw)

    # Render Jinja2 variables.
    # We use from_string() rather than from the FileSystemLoader so that the
    # rendered template can itself be tested without touching the filesystem.
    env = Environment(undefined=StrictUndefined, keep_trailing_newline=True)
    template = env.from_string(body)
    rendered = template.render(**variables)

    # Prepend few-shot examples ahead of the prompt body (before ---USER--- if present)
    if few_shot_examples:
        examples_block = _format_few_shot_block(few_shot_examples)
        # Insert examples before the ---USER--- delimiter so they appear in the
        # system prompt context, not duplicated into the user turn.
        if _USER_DELIMITER in rendered:
            system_part, user_part = rendered.split(_USER_DELIMITER, maxsplit=1)
            rendered = (
                system_part.rstrip("\n")
                + "\n\n"
                + examples_block
                + "\n\n"
                + _USER_DELIMITER
                + user_part
            )
        else:
            rendered = examples_block + "\n\n" + rendered

    return rendered


def get_prompt_path(prompt_name: str, prompt_version: str) -> Path:
    """Resolve and validate the file path for a named, versioned prompt.

    Convention: prompts/{prompt_name}_{prompt_version}.md
    Example:    prompts/classifier_v1.md

    Args:
        prompt_name:    Base name (e.g. "classifier").
        prompt_version: Version string (e.g. "v1").

    Returns:
        Resolved Path object pointing to the prompt file.

    Raises:
        FileNotFoundError: File does not exist at the expected path.
    """
    filename = f"{prompt_name}_{prompt_version}.md"
    path = PROMPTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Prompt file not found: {path}\n"
            f"Expected: prompts/{filename}\n"
            "Create the file or check your prompt_name / prompt_version arguments."
        )
    return path


def list_prompt_versions(prompt_name: str) -> list[str]:
    """Return all available version strings for a given prompt name, sorted ascending.

    Useful for the CLI's `hem status` command and for automated version tracking.

    Example:
        list_prompt_versions("classifier") → ["v1", "v2", "v3"]
    """
    pattern = f"{prompt_name}_v*.md"
    paths = sorted(PROMPTS_DIR.glob(pattern))
    return [p.stem.replace(f"{prompt_name}_", "") for p in paths]


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _strip_header(raw: str) -> str:
    """Remove the YAML front-matter block (between --- markers) from a prompt file.

    The header is optional. If no --- block is found at the start of the file,
    the raw string is returned unchanged.
    """
    return _HEADER_RE.sub("", raw).lstrip("\n")


def _format_few_shot_block(examples: list[dict]) -> str:
    """Format a list of few-shot examples as a structured Markdown block.

    Each example dict must have "input" and "output" keys. The formatted block
    is inserted into the prompt so the model sees concrete examples before the task.

    Args:
        examples: List of {"input": ..., "output": ..., "note": ...} dicts.
                  "note" is optional — used for human-readable context about the example.

    Returns:
        Multi-line string formatted as Markdown with numbered example sections.

    Example output:
        ## Validated Examples

        ### Example 1
        **Input:**
        {"name": "Cemvita", "description": "..."}
        **Output:**
        {"venture_scale_score": 0.87, "confidence": "HIGH", ...}
    """
    lines = ["## Validated Examples\n"]
    for i, example in enumerate(examples, start=1):
        lines.append(f"### Example {i}")
        if note := example.get("note"):
            lines.append(f"*{note}*\n")
        lines.append("**Input:**")
        lines.append(str(example.get("input", "(none)")))
        lines.append("\n**Output:**")
        lines.append(str(example.get("output", "(none)")))
        lines.append("")
    return "\n".join(lines)
