"""Stable local storage for deterministic result artifacts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .evaluation import canonical_json, content_hash
from .store import PromptStore


def save_result(store: PromptStore, category: str, stem: str, data: dict[str, Any]) -> str:
    folder = store.results_dir / category
    folder.mkdir(parents=True, exist_ok=True)
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "result"
    digest = content_hash(data)
    path = folder / f"{safe_stem}-{digest[:12]}.json"
    path.write_text(canonical_json(data), encoding="utf-8", newline="\n")
    return path.relative_to(store.root).as_posix()


def read_results(store: PromptStore, category: str) -> list[tuple[str, dict[str, Any]]]:
    folder = store.results_dir / category
    if not folder.exists():
        return []
    import json

    rows = []
    for path in sorted(folder.glob("*.json"), key=lambda item: item.name):
        data = json.loads(path.read_text(encoding="utf-8"))
        rows.append((path.relative_to(store.root).as_posix(), data))
    return rows
