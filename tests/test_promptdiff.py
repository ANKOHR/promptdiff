from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from promptdiff.artifacts import save_result
from promptdiff.audit import append_audit, read_audit
from promptdiff.diffing import build_diff
from promptdiff.errors import PromptRecordError, StoreError
from promptdiff.evaluation import canonical_json, evaluate_record
from promptdiff.fixtures import generate_fixtures
from promptdiff.gates import evaluate_gate
from promptdiff.ledger import build_ledger
from promptdiff.records import PromptRecord, parse_record, serialize_record
from promptdiff.reporting import HONESTY_BANNER, report_bytes
from promptdiff.store import PromptStore
from promptdiff.tokens import count_tokens


FIXED_DATE = "2026-09-29"
REPO_ROOT = Path(__file__).resolve().parents[1]


def demo(path: Path) -> PromptStore:
    generate_fixtures(path, created=FIXED_DATE)
    return PromptStore(path)


def test_store_versions_are_immutable_and_history_is_creation_ordered(tmp_path: Path) -> None:
    store = PromptStore(tmp_path)
    store.initialize()
    first = store.create_version("v1", created=FIXED_DATE)
    store.prompt_path.write_text(serialize_record(PromptRecord(**{**first.__dict__, "version": "draft", "body": "A revised deterministic prompt."})), encoding="utf-8")
    second = store.create_version("v2", created=FIXED_DATE)
    assert first.version == "v1"
    assert second.version == "v2"
    assert [item.version for item in store.list_versions()] == ["v1", "v2"]
    with pytest.raises(StoreError, match="immutable"):
        store.create_version("v1", created=FIXED_DATE)


def test_malformed_prompt_record_has_named_error() -> None:
    malformed = "---\nname: [broken\nversion: v1\nauthor: A\ncreated: 2026-09-29\ntags: []\n---\nbody"
    with pytest.raises(PromptRecordError, match="malformed YAML frontmatter"):
        parse_record(malformed)
    with pytest.raises(PromptRecordError, match="missing required field"):
        parse_record("---\nname: only-name\n---\nbody")


def test_diff_self_is_empty_and_seeded_change_has_expected_sections_and_tokens(tmp_path: Path) -> None:
    store = demo(tmp_path / "demo")
    same = build_diff(store.get_record("baseline"), store.get_record("baseline"))
    assert same["line_diff"] == []
    assert same["sections"] == {"added": [], "removed": [], "changed": []}
    assert same["tokens"]["delta"] == 0

    changed = build_diff(store.get_record("baseline"), store.get_record("improved"))
    assert changed["sections"] == {
        "added": ["Summary guidance"],
        "removed": ["Privacy"],
        "changed": ["Format"],
    }
    assert changed["tokens"]["delta"] == 8
    assert changed["line_diff"]


def test_fixture_generation_is_byte_deterministic(tmp_path: Path) -> None:
    left = generate_fixtures(tmp_path / "left", created=FIXED_DATE)
    right = generate_fixtures(tmp_path / "right", created=FIXED_DATE)
    left_files = {path.relative_to(left).as_posix(): path.read_bytes() for path in left.rglob("*") if path.is_file()}
    right_files = {path.relative_to(right).as_posix(): path.read_bytes() for path in right.rglob("*") if path.is_file()}
    assert left_files == right_files
    assert not any(b"http://" in content or b"https://" in content for content in left_files.values())


def test_baseline_passes_and_ambiguous_fixture_is_flagged(tmp_path: Path) -> None:
    store = demo(tmp_path / "demo")
    fixtures = store.load_fixtures_data()
    result = evaluate_record(store.get_record("baseline"), fixtures)
    assert result["summary"] == {"total": 7, "pass": 7, "warn": 0, "fail": 0}
    assert result["flakiness"] == {"checked": True, "identical": True, "runs_compared": 2}
    ambiguous = next(item for item in result["fixtures"] if item["id"] == "ambiguous-request")
    assert ambiguous["status"] == "pass"
    assert ambiguous["measured"]["flagged"] is True
    assert ambiguous["measured"]["finding"] == "warn"


def test_planted_regression_is_exact_and_improvement_has_evidence(tmp_path: Path) -> None:
    store = demo(tmp_path / "demo")
    fixtures = store.load_fixtures_data()
    baseline = evaluate_record(store.get_record("baseline"), fixtures)
    candidate = evaluate_record(store.get_record("improved"), fixtures, baseline)
    failed = [item["id"] for item in candidate["fixtures"] if item["status"] == "fail"]
    comparison = candidate["comparison"]
    assert failed == ["confidentiality"]
    assert [item["id"] for item in comparison["regressions"]] == ["confidentiality"]
    assert [item["id"] for item in comparison["improvements"]] == ["summary-wording"]
    regression_measurement = comparison["regressions"][0]["after"]["measured"]
    assert regression_measurement["found"] is False
    assert regression_measurement["expected_text"] == "Keep private notes confidential."
    assert comparison["regressions"][0]["after"]["rule"]["kind"] == "contains"


def test_same_eval_and_report_are_byte_identical(tmp_path: Path) -> None:
    store = demo(tmp_path / "demo")
    record = store.get_record("baseline")
    fixture_data = store.load_fixtures_data()
    first = canonical_json(evaluate_record(record, fixture_data)).encode("utf-8")
    second = canonical_json(evaluate_record(record, fixture_data)).encode("utf-8")
    assert first == second
    save_result(store, "eval", "baseline", json.loads(first))
    evaluate_gate(store, record, fixture_data)
    report_first = report_bytes(store, "json")
    report_second = report_bytes(store, "json")
    assert report_first == report_second
    assert HONESTY_BANNER.encode("utf-8") in report_first
    markdown_first = report_bytes(store, "md")
    append_audit(store, {"event_id": "report-only-change", "kind": "test"})
    assert report_bytes(store, "md") == markdown_first


def test_token_ledger_matches_hand_computed_fixture_values() -> None:
    assert count_tokens("alpha, beta!") == 4
    record = PromptRecord("toy", "v1", "Test", FIXED_DATE, (), "Hello world.")
    ledger = build_ledger(record, [{"id": "one", "input": "Hi!"}])
    assert ledger["prompt_tokens"] == 3
    assert ledger["runs"] == [{"fixture_id": "one", "prompt_tokens": 3, "fixture_input_tokens": 2, "tokens_per_run": 5}]
    assert ledger["average_tokens_per_run"] == 5
    assert ledger["latency"]["label"] == "estimate from declared profile"
    assert ledger["latency"]["estimated_average_ms"] == 100.25


def test_gate_promotes_clean_version_blocks_regression_and_budget_breach(tmp_path: Path) -> None:
    store = demo(tmp_path / "demo")
    fixtures = store.load_fixtures_data()
    clean = evaluate_gate(store, store.get_record("baseline"), fixtures)
    assert clean["decision"] == "promote"
    assert all(item["passed"] for item in clean["gates"])
    regressed = evaluate_gate(store, store.get_record("improved"), fixtures)
    assert regressed["decision"] == "block"
    assert regressed["comparison"]["summary"]["regression_count"] == 1
    bloated = evaluate_gate(store, store.get_record("cost-bloated"), fixtures)
    assert bloated["decision"] == "block"
    cost_gate = next(item for item in bloated["gates"] if item["name"] == "max_cost_per_run")
    assert cost_gate["measured"] > cost_gate["threshold"]["max_tokens"]
    audit = read_audit(store.audit_path)
    assert [item["kind"] for item in audit].count("gate") == 3
    assert audit[-1]["decision"] == "block"
    assert bloated["audit_event_id"] == audit[-1]["event_id"]


def test_gate_requires_a_baseline_for_regression_measurement(tmp_path: Path) -> None:
    store = PromptStore(tmp_path)
    store.initialize()
    record = store.create_version("v1", created=FIXED_DATE)
    result = evaluate_gate(store, record, store.load_fixtures_data())
    regression_gate = next(item for item in result["gates"] if item["name"] == "max_regressions")
    assert result["decision"] == "block"
    assert regression_gate["passed"] is False
    assert regression_gate["measured"] is None


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "promptdiff", *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )


def test_cli_smoke_for_every_subcommand(tmp_path: Path) -> None:
    generated = tmp_path / "generated"
    result = run_cli("fixtures", "--out", str(generated))
    assert result.returncode == 0, result.stderr
    assert "No language models are called" in result.stdout

    commands = [
        ("history", "--root", str(generated)),
        ("diff", "baseline", "improved", "--root", str(generated)),
        ("eval", "baseline", "--root", str(generated)),
        ("eval", "improved", "--against", "baseline", "--root", str(generated)),
        ("gate", "baseline", "--root", str(generated)),
        ("report", "--root", str(generated), "--format", "md"),
        ("report", "--root", str(generated), "--format", "json"),
    ]
    for command in commands:
        result = run_cli(*command)
        assert result.returncode == 0, f"{command}: {result.stderr}"
        assert "No language models are called" in result.stdout

    blocked = run_cli("gate", "improved", "--root", str(generated))
    assert blocked.returncode == 1
    assert json.loads(blocked.stdout)["decision"] == "block"

    initialized = tmp_path / "initialized"
    result = run_cli("init", "--root", str(initialized))
    assert result.returncode == 0, result.stderr
    result = run_cli("version", "--name", "v1", "--root", str(initialized))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["version"]["version"] == "v1"
    assert run_cli("diff", "v1", "v1", "--root", str(initialized)).returncode == 0
    assert run_cli("eval", "v1", "--root", str(initialized)).returncode == 0
    assert run_cli("gate", "v1", "--against", "v1", "--root", str(initialized)).returncode == 0
    assert run_cli("report", "--root", str(initialized), "--format", "json").returncode == 0
