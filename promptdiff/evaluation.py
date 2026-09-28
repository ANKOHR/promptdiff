"""Deterministic code-check evaluation and regression comparison."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .diffing import sections
from .errors import EvaluationError
from .fixtures import load_fixtures
from .ledger import build_ledger
from .records import PromptRecord


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _target_text(rule: dict[str, Any], body: str, fixture: dict[str, Any]) -> str:
    target = rule.get("target", "body")
    if target == "body":
        return body
    if target == "input":
        return fixture.get("input", "")
    if target == "description":
        return fixture["description"]
    if isinstance(target, str) and target.startswith("section:"):
        name = target.split(":", 1)[1]
        return sections(body).get(name, "")
    raise EvaluationError(f"unsupported rule target '{target}'")


def _evaluate_fixture(record: PromptRecord, fixture: dict[str, Any]) -> dict[str, Any]:
    rule = fixture["rule"]
    kind = rule["kind"]
    measured: dict[str, Any]
    if kind in {"contains", "not_contains"}:
        value = rule.get("value")
        if not isinstance(value, str):
            raise EvaluationError(f"fixture '{fixture['id']}' rule value must be a string")
        text = _target_text(rule, record.body, fixture)
        found = value in text
        passed = found if kind == "contains" else not found
        measured = {"target_characters": len(text), "expected_text": value, "found": found}
    elif kind == "contains_any":
        values = rule.get("values")
        if not isinstance(values, list) or not values or any(not isinstance(value, str) for value in values):
            raise EvaluationError(f"fixture '{fixture['id']}' rule values must be a non-empty list of strings")
        text = _target_text(rule, record.body, fixture)
        matched = [value for value in values if value in text]
        minimum = rule.get("min_matches", 1)
        if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 0:
            raise EvaluationError(f"fixture '{fixture['id']}' min_matches must be a non-negative integer")
        passed = len(matched) >= minimum
        measured = {"matched_values": matched, "matched_count": len(matched), "minimum_matches": minimum}
    elif kind == "regex":
        pattern = rule.get("pattern")
        if not isinstance(pattern, str):
            raise EvaluationError(f"fixture '{fixture['id']}' regex pattern must be a string")
        try:
            match = re.search(pattern, _target_text(rule, record.body, fixture))
        except re.error as exc:
            raise EvaluationError(f"fixture '{fixture['id']}' has invalid regex: {exc}") from exc
        passed = match is not None
        measured = {"pattern": pattern, "matched": passed, "match_text": match.group(0) if match else None}
    elif kind == "section_exists":
        expected = rule.get("name")
        if not isinstance(expected, str) or not expected:
            raise EvaluationError(f"fixture '{fixture['id']}' section name must be a non-empty string")
        names = list(sections(record.body))
        passed = expected in names
        measured = {"expected_section": expected, "observed_sections": names, "exists": passed}
    elif kind == "exact_match":
        expected = rule.get("value")
        if not isinstance(expected, str):
            raise EvaluationError(f"fixture '{fixture['id']}' exact-match value must be a string")
        observed = _target_text(rule, record.body, fixture)
        passed = observed == expected
        measured = {"expected": expected, "observed": observed, "exact": passed}
    elif kind == "expected_output":
        expected = fixture.get("expected_output")
        if not isinstance(expected, str):
            raise EvaluationError(f"fixture '{fixture['id']}' expected_output must be a string")
        declared = re.search(r"^\s*Example output:\s*(.*?)\s*$", record.body, flags=re.MULTILINE)
        observed = declared.group(1) if declared else None
        passed = observed == expected
        measured = {"expected_output": expected, "declared_output": observed, "exact": passed}
    elif kind == "flag_ambiguity":
        patterns = rule.get("patterns")
        if not isinstance(patterns, list) or not patterns or any(not isinstance(item, str) for item in patterns):
            raise EvaluationError(f"fixture '{fixture['id']}' ambiguity patterns must be a non-empty list of strings")
        text = fixture.get("input", "")
        detected = [pattern for pattern in patterns if pattern.casefold() in text.casefold()]
        flagged = len(detected) == len(patterns)
        passed = flagged
        measured = {"detected_patterns": detected, "pattern_count": len(patterns), "flagged": flagged, "finding": "warn" if flagged else "none"}
    else:
        raise EvaluationError(f"fixture '{fixture['id']}' has unsupported rule kind '{kind}'")

    severity = rule.get("severity", "fail")
    if severity not in {"fail", "warn"}:
        raise EvaluationError(f"fixture '{fixture['id']}' severity must be fail or warn")
    status = "pass" if passed else ("warn" if severity == "warn" else "fail")
    return {
        "id": fixture["id"],
        "description": fixture["description"],
        "status": status,
        "blocking": fixture.get("blocking", severity == "fail"),
        "measured": measured,
        "rule": rule,
    }


def _evaluate_core(record: PromptRecord, fixture_data: Any) -> dict[str, Any]:
    fixtures = load_fixtures(fixture_data)
    results = [_evaluate_fixture(record, fixture) for fixture in fixtures]
    counts = {name: sum(result["status"] == name for result in results) for name in ("pass", "warn", "fail")}
    return {
        "schema_version": 1,
        "kind": "evaluation",
        "version": record.version,
        "fixtures": results,
        "summary": {"total": len(results), **counts},
        "ledger": build_ledger(record, fixtures),
    }


def compare_evaluations(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    before = {item["id"]: item for item in baseline["fixtures"]}
    after = {item["id"]: item for item in candidate["fixtures"]}
    regressions = []
    improvements = []
    rank = {"fail": 0, "warn": 1, "pass": 2}
    for fixture_id in before:
        if fixture_id not in after:
            regressions.append({"id": fixture_id, "reason": "fixture missing from candidate", "before": before[fixture_id], "after": None})
            continue
        left = before[fixture_id]
        right = after[fixture_id]
        if rank[right["status"]] < rank[left["status"]]:
            regressions.append({"id": fixture_id, "reason": "status declined", "before": left, "after": right})
            continue
        before_count = left.get("measured", {}).get("matched_count")
        after_count = right.get("measured", {}).get("matched_count")
        if isinstance(before_count, int) and isinstance(after_count, int) and after_count < before_count:
            regressions.append({"id": fixture_id, "reason": "matched count decreased", "before": left, "after": right})
            continue
        if rank[right["status"]] > rank[left["status"]] or (
            isinstance(before_count, int) and isinstance(after_count, int) and after_count > before_count
        ):
            improvements.append({"id": fixture_id, "reason": "status improved" if rank[right["status"]] > rank[left["status"]] else "matched count increased", "before": left, "after": right})
    for fixture_id in after:
        if fixture_id not in before:
            improvements.append({"id": fixture_id, "reason": "new candidate fixture", "before": None, "after": after[fixture_id]})
    return {
        "baseline": baseline["version"],
        "candidate": candidate["version"],
        "regressions": regressions,
        "improvements": improvements,
        "summary": {"regression_count": len(regressions), "improvement_count": len(improvements)},
    }


def evaluate_record(record: PromptRecord, fixture_data: Any, against: dict[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate twice and report a deterministic flakiness check."""
    first = _evaluate_core(record, fixture_data)
    second = _evaluate_core(record, fixture_data)
    first_bytes = canonical_json(first).encode("utf-8")
    second_bytes = canonical_json(second).encode("utf-8")
    result = dict(first)
    result["flakiness"] = {"checked": True, "identical": first_bytes == second_bytes, "runs_compared": 2}
    if against is not None:
        result["comparison"] = compare_evaluations(against, first)
    return result


def content_hash(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()
