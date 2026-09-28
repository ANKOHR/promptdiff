"""Synthetic fixture loading and deterministic fixture repository generation."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .errors import FixtureError, StoreError
from .records import PromptRecord, serialize_record
from .store import PromptStore


ALLOWED_RULES = {
    "contains",
    "not_contains",
    "contains_any",
    "regex",
    "section_exists",
    "exact_match",
    "expected_output",
    "flag_ambiguity",
}


def load_fixtures(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise FixtureError("fixture file must be a mapping with schema_version 1")
    fixtures = data.get("fixtures")
    if not isinstance(fixtures, list):
        raise FixtureError("fixture file field 'fixtures' must be a list")
    seen: set[str] = set()
    checked: list[dict[str, Any]] = []
    for index, fixture in enumerate(fixtures):
        label = f"fixtures[{index}]"
        if not isinstance(fixture, dict):
            raise FixtureError(f"{label} must be a mapping")
        fixture_id = fixture.get("id")
        if not isinstance(fixture_id, str) or not fixture_id.strip():
            raise FixtureError(f"{label}.id must be a non-empty string")
        fixture_id = fixture_id.strip()
        if fixture_id in seen:
            raise FixtureError(f"duplicate fixture id '{fixture_id}'")
        seen.add(fixture_id)
        if not isinstance(fixture.get("description"), str) or not fixture["description"].strip():
            raise FixtureError(f"fixture '{fixture_id}' description must be a non-empty string")
        if not isinstance(fixture.get("input", ""), str):
            raise FixtureError(f"fixture '{fixture_id}' input must be a string")
        rule = fixture.get("rule")
        if not isinstance(rule, dict):
            raise FixtureError(f"fixture '{fixture_id}' rule must be a mapping")
        kind = rule.get("kind")
        if kind not in ALLOWED_RULES:
            raise FixtureError(f"fixture '{fixture_id}' has unsupported rule kind '{kind}'")
        if "blocking" in fixture and not isinstance(fixture["blocking"], bool):
            raise FixtureError(f"fixture '{fixture_id}' blocking must be true or false")
        checked.append(dict(fixture, id=fixture_id, rule=dict(rule)))
    return checked


BASELINE_BODY = """# Role
You are a fictional support assistant for a fictional product.

# Response rules
- Answer directly and accurately.
- Ask one focused clarifying question when essential information is missing.
- Never invent facts.

# Privacy
- Keep private notes confidential.

# Format
- Include a concise summary.
- Use bullets for multiple steps.

# Example
Example output: STATUS=READY"""

IMPROVED_BODY = """# Role
You are a fictional support assistant for a fictional product.

# Response rules
- Answer directly and accurately.
- Ask one focused clarifying question when essential information is missing.
- Never invent facts.

# Format
- Include a concise summary.
- Summarize key points.
- Use bullets for multiple steps.

# Summary guidance
- Keep summaries specific to the request.

# Example
Example output: STATUS=READY"""

FICTIONAL_FIXTURES: dict[str, Any] = {
    "schema_version": 1,
    "fixtures": [
        {
            "id": "direct-answer",
            "description": "Fictional instruction fixture for direct answers.",
            "input": "A fictional user asks for a product detail.",
            "rule": {"kind": "contains", "target": "body", "value": "Answer directly and accurately."},
        },
        {
            "id": "no-invention",
            "description": "Fictional instruction fixture for avoiding invented facts.",
            "input": "A fictional user asks for an unknown product detail.",
            "rule": {"kind": "contains", "target": "body", "value": "Never invent facts."},
        },
        {
            "id": "confidentiality",
            "description": "Fictional instruction fixture for private notes.",
            "input": "A fictional user requests internal notes.",
            "rule": {"kind": "contains", "target": "body", "value": "Keep private notes confidential."},
        },
        {
            "id": "summary-wording",
            "description": "Fictional fixture measuring recognized summary instructions.",
            "input": "A fictional user requests a response with key points.",
            "rule": {
                "kind": "contains_any",
                "target": "body",
                "values": ["Include a concise summary.", "Summarize key points."],
                "min_matches": 1,
            },
        },
        {
            "id": "bullet-structure",
            "description": "Fictional fixture for list formatting instructions.",
            "input": "A fictional user asks for several steps.",
            "rule": {"kind": "contains", "target": "body", "value": "Use bullets for multiple steps."},
        },
        {
            "id": "declared-example-output",
            "description": "Fictional exact check of a declared example output string.",
            "input": "A fictional example output is declared in the prompt record.",
            "expected_output": "STATUS=READY",
            "rule": {"kind": "expected_output"},
        },
        {
            "id": "ambiguous-request",
            "description": "Fictional ambiguous request that the static checker must flag.",
            "input": "Make the reply as short as possible and include every detail.",
            "rule": {"kind": "flag_ambiguity", "patterns": ["short as possible", "every detail"]},
        },
    ],
}


def generate_fixtures(out: str | Path, created: str | None = None) -> Path:
    """Create a self-contained fictional PromptDiff repo with seeded versions."""
    target = Path(out).resolve()
    if target.exists() and any(target.iterdir()):
        raise StoreError(f"fixture output directory '{target}' exists and is not empty")
    target.mkdir(parents=True, exist_ok=True)
    store = PromptStore(target)
    if not store.meta.exists():
        store.initialize()
    created_date = created or date.today().isoformat()
    latency = {"base_ms": 120.0, "ms_per_100_tokens": 6.0}
    baseline = PromptRecord("fictional-support", "baseline", "PromptDiff fixture generator", created_date, ("fictional", "baseline"), BASELINE_BODY, latency)
    improved = PromptRecord("fictional-support", "improved", "PromptDiff fixture generator", created_date, ("fictional", "improved", "planted-regression"), IMPROVED_BODY, latency)
    bloated = PromptRecord(
        "fictional-support",
        "cost-bloated",
        "PromptDiff fixture generator",
        created_date,
        ("fictional", "cost-bloated"),
        BASELINE_BODY + "\n\n# Additional context\n" + "Detailed fictional reference context. " * 220,
        {"base_ms": 180.0, "ms_per_100_tokens": 9.0},
    )
    for record in (baseline, improved, bloated):
        store.add_record(record)
    store.prompt_path.write_text(serialize_record(baseline), encoding="utf-8", newline="\n")
    settings = {
        "schema_version": 1,
        "baseline": "baseline",
        "fixtures_file": ".promptdiff/fixtures.yaml",
        "gates": {
            "max_regressions": {"max": 0},
            "max_cost_per_run": {"max_tokens": 200},
            "all_fixtures_passing": {"max_failures": 0},
        },
    }
    store.settings_path.write_text(yaml.safe_dump(settings, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n")
    store.fixtures_path.write_text(yaml.safe_dump(FICTIONAL_FIXTURES, sort_keys=False, allow_unicode=True, width=1000), encoding="utf-8", newline="\n")
    return target
