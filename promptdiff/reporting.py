"""Deterministic Markdown and JSON release reports."""

from __future__ import annotations

import json
from typing import Any

from .artifacts import read_results
from .evaluation import canonical_json
from .store import PromptStore


HONESTY_BANNER = "Deterministic code checks on synthetic fixtures. No language models are called. Token counts are heuristics, and latency is estimated from declared profiles."


def build_report(store: PromptStore) -> dict[str, Any]:
    evaluations = read_results(store, "eval")
    gates = read_results(store, "gate")
    return {
        "schema_version": 1,
        "kind": "report",
        "honesty_banner": HONESTY_BANNER,
        "evaluations": [
            {"evidence": path, "result": data}
            for path, data in evaluations
        ],
        "gates": [
            {"evidence": path, "audit_log": "pd-audit.jsonl", "audit_event_id": data.get("audit_event_id"), "result": data}
            for path, data in gates
        ],
    }


def render_report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# PromptDiff release report",
        "",
        f"> {report['honesty_banner']}",
        "",
        "Evals are deterministic code checks on synthetic fixtures, not model judgments.",
        "",
        "## Evaluation runs",
        "",
        "| Version | Pass | Warn | Fail | Average heuristic tokens per run | Estimated latency | Evidence |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    if not report["evaluations"]:
        lines.append("| none | 0 | 0 | 0 | 0 | not available | none |")
    for row in report["evaluations"]:
        result = row["result"]
        summary = result["summary"]
        ledger = result["ledger"]
        lines.append(
            f"| {result['version']} | {summary['pass']} | {summary['warn']} | {summary['fail']} | "
            f"{ledger['average_tokens_per_run']} | {ledger['latency']['estimated_average_ms']} ms estimate | `{row['evidence']}` |"
        )
    lines.extend(["", "## Fixture evidence", "", "| Version | Fixture | Status | Measured values | Rule | Artifact |", "| --- | --- | --- | --- | --- | --- |"])
    if not report["evaluations"]:
        lines.append("| none | none | not run | none | none | none |")
    for row in report["evaluations"]:
        result = row["result"]
        for fixture in result["fixtures"]:
            measured = json.dumps(fixture["measured"], ensure_ascii=False, sort_keys=True)
            rule = json.dumps(fixture["rule"], ensure_ascii=False, sort_keys=True)
            lines.append(f"| {result['version']} | {fixture['id']} | {fixture['status']} | `{measured}` | `{rule}` | `{row['evidence']}` |")

    lines.extend(["", "## Release gates", "", "| Version | Decision | Evidence | Audit event |", "| --- | --- | --- | --- |"])
    if not report["gates"]:
        lines.append("| none | not run | none | none |")
    for row in report["gates"]:
        result = row["result"]
        lines.append(f"| {result['version']} | {result['decision']} | `{row['evidence']}` | `{row['audit_event_id']}` |")

    lines.extend(["", "## Gate measurements", "", "| Version | Gate | Decision | Measured | Threshold | Evidence |", "| --- | --- | --- | ---: | --- | --- |"])
    if not report["gates"]:
        lines.append("| none | none | not run | none | none | none |")
    for row in report["gates"]:
        result = row["result"]
        evaluation_ref = result.get("evidence", {}).get("evaluation", "none")
        for gate in result["gates"]:
            measured = "not measured" if gate["measured"] is None else gate["measured"]
            threshold = json.dumps(gate["threshold"], ensure_ascii=False, sort_keys=True)
            lines.append(f"| {result['version']} | {gate['name']} | {'pass' if gate['passed'] else 'fail'} | {measured} | `{threshold}` | `{evaluation_ref}` |")

    lines.extend(["", "## Regression and improvement evidence", "", "| Version | Baseline | Regressions | Improvements | Evidence |", "| --- | --- | --- | --- | --- |"])
    comparisons = [row for row in report["gates"] if row["result"].get("comparison") is not None]
    if not comparisons:
        lines.append("| none | none | no baseline comparison | none | none |")
    for row in comparisons:
        result = row["result"]
        comparison = result["comparison"]
        regressions = ", ".join(item["id"] for item in comparison["regressions"]) or "none"
        improvements = ", ".join(item["id"] for item in comparison["improvements"]) or "none"
        lines.append(f"| {result['version']} | {comparison['baseline']} | {regressions} | {improvements} | `{row['evidence']}` |")
    lines.extend(["", "## Evidence notes", "", "Token counts use a documented lexical heuristic and are not billing measurements. Latency values are estimates from each prompt version's declared profile.", ""])
    return "\n".join(lines)


def report_bytes(store: PromptStore, output_format: str) -> bytes:
    report = build_report(store)
    if output_format == "json":
        return canonical_json(report).encode("utf-8")
    if output_format == "md":
        return render_report_markdown(report).encode("utf-8")
    raise ValueError(f"unsupported report format '{output_format}'")
