"""Append-only local audit log for evaluation and gate decisions."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .store import PromptStore


def append_audit(store: PromptStore, event: dict[str, Any]) -> None:
    store.root.mkdir(parents=True, exist_ok=True)
    entry = dict(event)
    entry["recorded_at_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    with store.audit_path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def read_audit(path: str | Path) -> list[dict[str, Any]]:
    audit_path = Path(path)
    if not audit_path.exists():
        return []
    entries = []
    for line_number, line in enumerate(audit_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"audit log line {line_number} is malformed") from exc
        if not isinstance(item, dict):
            raise ValueError(f"audit log line {line_number} must be a JSON object")
        entries.append(item)
    return entries
