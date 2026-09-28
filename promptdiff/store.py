"""Local immutable prompt version store."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
import json
from pathlib import Path
from typing import Any

import yaml

from .errors import PromptRecordError, StoreError
from .records import PromptRecord, VERSION_PATTERN, parse_record, serialize_record


DEFAULT_SETTINGS: dict[str, Any] = {
    "schema_version": 1,
    "baseline": None,
    "fixtures_file": ".promptdiff/fixtures.yaml",
    "gates": {
        "max_regressions": {"max": 0},
        "max_cost_per_run": {"max_tokens": 200},
        "all_fixtures_passing": {"max_failures": 0},
    },
}

STARTER_BODY = "# Instructions\nAnswer clearly and accurately."
STARTER_FIXTURES: dict[str, Any] = {
    "schema_version": 1,
    "fixtures": [
        {
            "id": "clear-answer",
            "description": "Fictional fixture requiring a clear-answer instruction.",
            "input": "A fictional user asks a simple question.",
            "rule": {"kind": "contains", "target": "body", "value": "Answer clearly and accurately."},
        }
    ],
}


class PromptStore:
    """Filesystem-backed repository rooted at a user selected directory."""

    def __init__(self, root: str | Path = ".") -> None:
        self.root = Path(root).resolve()
        self.meta = self.root / ".promptdiff"
        self.versions_dir = self.meta / "versions"
        self.results_dir = self.meta / "results"
        self.prompt_path = self.root / "prompt.md"
        self.history_path = self.meta / "history.json"
        self.settings_path = self.meta / "config.yaml"
        self.fixtures_path = self.meta / "fixtures.yaml"
        self.audit_path = self.root / "pd-audit.jsonl"

    def initialize(self) -> None:
        if self.meta.exists() or self.prompt_path.exists():
            raise StoreError("PromptDiff is already initialized or prompt.md already exists")
        self.versions_dir.mkdir(parents=True)
        (self.results_dir / "eval").mkdir(parents=True)
        (self.results_dir / "gate").mkdir(parents=True)
        today = date.today().isoformat()
        starter = PromptRecord(
            name="my-prompt",
            version="draft",
            author="PromptDiff user",
            created=today,
            tags=("draft",),
            body=STARTER_BODY,
            latency={"base_ms": 100.0, "ms_per_100_tokens": 5.0},
        )
        self.prompt_path.write_text(serialize_record(starter), encoding="utf-8", newline="\n")
        self.settings_path.write_text(yaml.safe_dump(DEFAULT_SETTINGS, sort_keys=False), encoding="utf-8", newline="\n")
        self.fixtures_path.write_text(yaml.safe_dump(STARTER_FIXTURES, sort_keys=False, allow_unicode=True), encoding="utf-8", newline="\n")
        self.history_path.write_text("[]\n", encoding="utf-8", newline="\n")

    def read_settings(self) -> dict[str, Any]:
        if not self.settings_path.exists():
            raise StoreError("PromptDiff config is missing; run 'promptdiff init'")
        try:
            settings = yaml.safe_load(self.settings_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise StoreError(f"malformed config YAML: {exc.__class__.__name__}") from exc
        if not isinstance(settings, dict) or settings.get("schema_version") != 1:
            raise StoreError("config must be a mapping with schema_version 1")
        return settings

    def read_draft(self) -> PromptRecord:
        if not self.prompt_path.exists():
            raise StoreError("prompt.md is missing; run 'promptdiff init'")
        return parse_record(self.prompt_path.read_text(encoding="utf-8"))

    def create_version(self, version_name: str, created: str | None = None) -> PromptRecord:
        if not VERSION_PATTERN.fullmatch(version_name):
            raise StoreError("version name must use 1 to 64 letters, digits, dots, underscores, or hyphens and start with a letter or digit")
        draft = self.read_draft()
        record = replace(draft, version=version_name, created=created or date.today().isoformat())
        return self.add_record(record)

    def add_record(self, record: PromptRecord) -> PromptRecord:
        """Add a prebuilt record, preserving the same immutability checks."""
        if not VERSION_PATTERN.fullmatch(record.version):
            raise StoreError(f"invalid version name '{record.version}'")
        path = self.versions_dir / f"{record.version}.md"
        if path.exists():
            raise StoreError(f"version '{record.version}' already exists and versions are immutable")
        self.versions_dir.mkdir(parents=True, exist_ok=True)
        try:
            history = json.loads(self.history_path.read_text(encoding="utf-8")) if self.history_path.exists() else []
        except (json.JSONDecodeError, OSError) as exc:
            raise StoreError("history index is malformed") from exc
        if not isinstance(history, list):
            raise StoreError("history index must be a list")
        if any(isinstance(item, dict) and item.get("version") == record.version for item in history):
            raise StoreError(f"version '{record.version}' already exists and versions are immutable")
        path.write_text(serialize_record(record), encoding="utf-8", newline="\n")
        history.append(record.metadata())
        self.history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        return record

    def get_record(self, version_name: str) -> PromptRecord:
        if not VERSION_PATTERN.fullmatch(version_name):
            raise StoreError(f"invalid version name '{version_name}'")
        path = self.versions_dir / f"{version_name}.md"
        if not path.exists():
            raise StoreError(f"version '{version_name}' was not found")
        record = parse_record(path.read_text(encoding="utf-8"))
        if record.version != version_name:
            raise PromptRecordError(f"stored version file '{path.name}' declares version '{record.version}'")
        return record

    def list_versions(self) -> list[PromptRecord]:
        if not self.versions_dir.exists():
            return []
        try:
            entries = json.loads(self.history_path.read_text(encoding="utf-8")) if self.history_path.exists() else []
        except (json.JSONDecodeError, OSError) as exc:
            raise StoreError("history index is malformed") from exc
        if not isinstance(entries, list):
            raise StoreError("history index must be a list")
        names: list[str] = []
        for item in entries:
            if isinstance(item, dict) and isinstance(item.get("version"), str):
                names.append(item["version"])
        seen = set(names)
        names.extend(path.stem for path in sorted(self.versions_dir.glob("*.md")) if path.stem not in seen)
        return [self.get_record(name) for name in names]

    def load_fixtures_data(self) -> Any:
        path = Path(self.read_settings().get("fixtures_file", ".promptdiff/fixtures.yaml"))
        if not path.is_absolute():
            path = self.root / path
        if not path.exists():
            raise StoreError(f"fixture file '{path}' was not found")
        try:
            return yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise StoreError(f"malformed fixture YAML: {exc.__class__.__name__}") from exc
