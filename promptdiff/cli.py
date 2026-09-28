"""Command line interface for PromptDiff."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from .artifacts import save_result
from .audit import append_audit
from .diffing import build_diff, render_diff_markdown
from .errors import PromptDiffError
from .evaluation import canonical_json, content_hash, evaluate_record
from .fixtures import generate_fixtures
from .gates import evaluate_gate
from .records import PromptRecord
from .reporting import HONESTY_BANNER, build_report, render_report_markdown
from .store import PromptStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="promptdiff", description="Version, diff, evaluate, and gate prompt releases with deterministic code checks.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_root(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument("--root", default=".", help="PromptDiff repository directory")

    init_parser = subparsers.add_parser("init", help="initialize a local PromptDiff repository")
    add_root(init_parser)

    version_parser = subparsers.add_parser("version", help="save the current prompt draft as an immutable version")
    add_root(version_parser)
    version_parser.add_argument("--name", required=True, help="immutable version name")

    history_parser = subparsers.add_parser("history", help="list versions in creation order")
    add_root(history_parser)

    diff_parser = subparsers.add_parser("diff", help="diff two saved prompt versions")
    add_root(diff_parser)
    diff_parser.add_argument("from_version")
    diff_parser.add_argument("to_version")
    diff_parser.add_argument("--format", choices=("json", "md"), default="json")

    eval_parser = subparsers.add_parser("eval", help="run deterministic fixture checks for a version")
    add_root(eval_parser)
    eval_parser.add_argument("version")
    eval_parser.add_argument("--against", help="compare fixture outcomes with this baseline version")
    eval_parser.add_argument("--format", choices=("json", "md"), default="json")

    gate_parser = subparsers.add_parser("gate", help="evaluate configured release gates")
    add_root(gate_parser)
    gate_parser.add_argument("version")
    gate_parser.add_argument("--against", help="override the configured comparison baseline")
    gate_parser.add_argument("--format", choices=("json", "md"), default="json")

    report_parser = subparsers.add_parser("report", help="render stored eval and gate evidence")
    add_root(report_parser)
    report_parser.add_argument("--format", choices=("md", "json"), default="md")
    report_parser.add_argument("--out", help="write the report to this path")

    fixtures_parser = subparsers.add_parser("fixtures", help="generate fictional synthetic prompt fixtures")
    fixtures_parser.add_argument("--out", required=True, help="output directory for the generated repository")

    return parser


def _eval_markdown(result: dict[str, Any]) -> str:
    summary = result["summary"]
    ledger = result["ledger"]
    lines = [
        f"# Evaluation: {result['version']}",
        "",
        f"> {HONESTY_BANNER}",
        "",
        f"Pass: {summary['pass']}, warn: {summary['warn']}, fail: {summary['fail']}.",
        f"Average heuristic tokens per run: {ledger['average_tokens_per_run']}.",
        f"Estimated average latency: {ledger['latency']['estimated_average_ms']} ms from the declared profile.",
        f"Flakiness check identical: {str(result['flakiness']['identical']).lower()}.",
        "",
        "| Fixture | Status | Measured values | Rule |",
        "| --- | --- | --- | --- |",
    ]
    for item in result["fixtures"]:
        lines.append(f"| {item['id']} | {item['status']} | `{json.dumps(item['measured'], ensure_ascii=False, sort_keys=True)}` | `{json.dumps(item['rule'], ensure_ascii=False, sort_keys=True)}` |")
    if "comparison" in result:
        comparison = result["comparison"]
        lines.extend(["", f"Regressions: {comparison['summary']['regression_count']}. Improvements: {comparison['summary']['improvement_count']}."])
    return "\n".join(lines) + "\n"


def _gate_markdown(result: dict[str, Any]) -> str:
    lines = [
        f"# Release gate: {result['version']}",
        "",
        f"> {HONESTY_BANNER}",
        "",
        f"Decision: **{result['decision']}**",
        "",
        "| Gate | Decision | Measured | Threshold |",
        "| --- | --- | ---: | --- |",
    ]
    for item in result["gates"]:
        lines.append(f"| {item['name']} | {'pass' if item['passed'] else 'fail'} | {item['measured']} | `{json.dumps(item['threshold'], sort_keys=True)}` |")
    lines.extend(["", f"Audit event: `{result['audit_event_id']}` in `{result['evidence']['audit_log']}`.", ""])
    return "\n".join(lines)


def _emit_json(data: Any) -> None:
    sys.stdout.write(canonical_json(data))


def _eval_command(store: PromptStore, version: str, against: str | None) -> dict[str, Any]:
    fixture_data = store.load_fixtures_data()
    record = store.get_record(version)
    baseline_eval = None
    if against:
        baseline_record = store.get_record(against)
        baseline_eval = evaluate_record(baseline_record, fixture_data)
        save_result(store, "eval", f"{baseline_record.version}-against", baseline_eval)
    result = evaluate_record(record, fixture_data, baseline_eval)
    result["honesty_banner"] = HONESTY_BANNER
    artifact = save_result(store, "eval", version + (f"-against-{against}" if against else ""), result)
    append_audit(store, {
        "event_id": "eval:" + content_hash(result)[:16],
        "kind": "evaluation",
        "version": version,
        "against": against,
        "result_hash": content_hash(result),
        "artifact": artifact,
        "trigger": "eval",
    })
    return result


def run(args: argparse.Namespace) -> int:
    if args.command == "fixtures":
        target = generate_fixtures(args.out)
        sys.stdout.write(f"Generated fictional PromptDiff versions and fixture cases in {target}.\n{HONESTY_BANNER}\n")
        return 0

    store = PromptStore(args.root)
    if args.command == "init":
        store.initialize()
        sys.stdout.write(f"Initialized PromptDiff in {store.root}.\n{HONESTY_BANNER}\n")
        return 0
    if args.command == "version":
        record = store.create_version(args.name)
        _emit_json({"honesty_banner": HONESTY_BANNER, "version": record.metadata()})
        return 0
    if args.command == "history":
        _emit_json({"honesty_banner": HONESTY_BANNER, "versions": [record.metadata() for record in store.list_versions()]})
        return 0
    if args.command == "diff":
        data = build_diff(store.get_record(args.from_version), store.get_record(args.to_version))
        data["honesty_banner"] = HONESTY_BANNER
        if args.format == "json":
            _emit_json(data)
        else:
            sys.stdout.write(f"> {HONESTY_BANNER}\n\n{render_diff_markdown(data)}")
        return 0
    if args.command == "eval":
        data = _eval_command(store, args.version, args.against)
        if args.format == "json":
            _emit_json(data)
        else:
            sys.stdout.write(_eval_markdown(data))
        return 0
    if args.command == "gate":
        record = store.get_record(args.version)
        data = evaluate_gate(store, record, store.load_fixtures_data(), args.against)
        if args.format == "json":
            _emit_json(data)
        else:
            sys.stdout.write(_gate_markdown(data))
        return 0 if data["decision"] == "promote" else 1
    if args.command == "report":
        report = build_report(store)
        output = canonical_json(report) if args.format == "json" else render_report_markdown(report)
        if args.out:
            destination = Path(args.out)
            if not destination.is_absolute():
                destination = store.root / destination
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(output, encoding="utf-8", newline="\n")
            sys.stdout.write(f"Wrote deterministic report to {destination}.\n{HONESTY_BANNER}\n")
        else:
            sys.stdout.write(output)
        return 0
    raise PromptDiffError(f"unsupported command '{args.command}'")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except (PromptDiffError, OSError, ValueError) as exc:
        sys.stderr.write(f"promptdiff: {exc.__class__.__name__}: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
