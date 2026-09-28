"""Prompt token and declared latency estimate ledger."""

from __future__ import annotations

from typing import Any

from .records import PromptRecord
from .tokens import count_tokens


def build_ledger(record: PromptRecord, fixtures: list[dict[str, Any]]) -> dict[str, Any]:
    prompt_tokens = count_tokens(record.body)
    fixture_rows = []
    for fixture in fixtures:
        input_tokens = count_tokens(fixture.get("input", ""))
        fixture_rows.append({
            "fixture_id": fixture["id"],
            "prompt_tokens": prompt_tokens,
            "fixture_input_tokens": input_tokens,
            "tokens_per_run": prompt_tokens + input_tokens,
        })
    totals = [row["tokens_per_run"] for row in fixture_rows]
    count = len(totals)
    average = sum(totals) / count if count else 0.0
    profile = record.latency or {"base_ms": 100.0, "ms_per_100_tokens": 5.0}
    estimated_latency = profile["base_ms"] + (average / 100.0) * profile["ms_per_100_tokens"]
    return {
        "counting_rule": "unicode-word-runs-and-individual-punctuation; prompt body plus fixture input per run",
        "prompt_tokens": prompt_tokens,
        "fixture_count": count,
        "total_tokens_across_fixtures": sum(totals),
        "average_tokens_per_run": round(average, 6),
        "min_tokens_per_run": min(totals) if totals else 0,
        "max_tokens_per_run": max(totals) if totals else 0,
        "runs": fixture_rows,
        "latency": {
            "label": "estimate from declared profile",
            "profile": {"base_ms": profile["base_ms"], "ms_per_100_tokens": profile["ms_per_100_tokens"]},
            "estimated_average_ms": round(estimated_latency, 6),
        },
    }
