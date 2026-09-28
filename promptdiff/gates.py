"""Measured release gates and auditable promotion decisions."""

from __future__ import annotations

import math
from typing import Any

from .artifacts import save_result
from .audit import append_audit
from .errors import PromptDiffError
from .evaluation import content_hash, evaluate_record
from .fixtures import load_fixtures
from .records import PromptRecord
from .reporting import HONESTY_BANNER
from .store import PromptStore


def evaluate_gate(
    store: PromptStore,
    record: PromptRecord,
    fixture_data: Any,
    against: str | None = None,
) -> dict[str, Any]:
    fixtures = load_fixtures(fixture_data)
    if not fixtures:
        raise PromptDiffError("release gates require at least one fixture")
    settings = store.read_settings()
    baseline_name = against or settings.get("baseline")
    baseline_eval = None
    baseline_ref = None
    if baseline_name:
        baseline_record = store.get_record(str(baseline_name))
        baseline_eval = evaluate_record(baseline_record, fixture_data)
        baseline_eval["honesty_banner"] = HONESTY_BANNER
        baseline_ref = save_result(store, "eval", f"{baseline_record.version}-gate-baseline", baseline_eval)
    eval_result = evaluate_record(record, fixture_data, baseline_eval)
    eval_result["honesty_banner"] = HONESTY_BANNER
    eval_ref = save_result(store, "eval", f"{record.version}-gate", eval_result)
    comparison = eval_result.get("comparison")

    configured = settings.get("gates", {})
    if not isinstance(configured, dict):
        raise PromptDiffError("config field 'gates' must be a mapping")
    allowed_gates = {"max_regressions", "max_cost_per_run", "all_fixtures_passing"}
    unknown_gates = sorted(set(configured) - allowed_gates)
    if unknown_gates:
        raise PromptDiffError(f"unsupported gate name(s): {', '.join(unknown_gates)}")

    def threshold(gate_name: str, threshold_name: str, default: int) -> int | float:
        gate_config = configured.get(gate_name, {})
        if not isinstance(gate_config, dict):
            raise PromptDiffError(f"gate '{gate_name}' config must be a mapping")
        value = gate_config.get(threshold_name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or value < 0:
            raise PromptDiffError(f"gate threshold '{gate_name}.{threshold_name}' must be a finite non-negative number")
        return value

    regression_limit = threshold("max_regressions", "max", 0)
    token_limit = threshold("max_cost_per_run", "max_tokens", 200)
    failure_limit = threshold("all_fixtures_passing", "max_failures", 0)

    regression_count = comparison["summary"]["regression_count"] if comparison else None
    average_tokens = eval_result["ledger"]["average_tokens_per_run"]
    blocking_failures = sum(item["status"] == "fail" and item.get("blocking", True) for item in eval_result["fixtures"])
    warning_count = eval_result["summary"]["warn"]
    checks = [
        {
            "name": "max_regressions",
            "threshold": {"max": regression_limit, "baseline": baseline_name},
            "measured": regression_count,
            "passed": regression_count is not None and regression_count <= regression_limit,
            "evidence": "comparison.regressions" if comparison else "baseline required",
        },
        {
            "name": "max_cost_per_run",
            "threshold": {"max_tokens": token_limit, "unit": "heuristic tokens per run"},
            "measured": average_tokens,
            "passed": average_tokens <= token_limit,
            "evidence": "evaluation.ledger.average_tokens_per_run",
        },
        {
            "name": "all_fixtures_passing",
            "threshold": {"max_failures": failure_limit, "warning_semantics": "warning findings are visible and non-blocking when the rule passes or fixture is marked non-blocking"},
            "measured": blocking_failures,
            "passed": blocking_failures <= failure_limit,
            "warning_count": warning_count,
            "evidence": "evaluation.fixtures",
        },
    ]
    result: dict[str, Any] = {
        "schema_version": 1,
        "kind": "gate",
        "honesty_banner": HONESTY_BANNER,
        "version": record.version,
        "decision": "promote" if all(item["passed"] for item in checks) else "block",
        "baseline": baseline_name,
        "gates": checks,
        "comparison": comparison,
        "evaluation_summary": eval_result["summary"],
        "ledger": eval_result["ledger"],
        "evidence": {"evaluation": eval_ref, "baseline_evaluation": baseline_ref, "audit_log": "pd-audit.jsonl"},
    }
    audit_id = "gate:" + content_hash(result)[:16]
    result["audit_event_id"] = audit_id
    append_audit(store, {
        "event_id": "eval:" + content_hash(eval_result)[:16],
        "kind": "evaluation",
        "version": record.version,
        "result_hash": content_hash(eval_result),
        "artifact": eval_ref,
        "trigger": "gate",
    })
    append_audit(store, {
        "event_id": audit_id,
        "kind": "gate",
        "version": record.version,
        "decision": result["decision"],
        "baseline": baseline_name,
        "gates": checks,
        "evaluation_artifact": eval_ref,
    })
    save_result(store, "gate", record.version, result)
    return result
