"""Stable line-level and section-level prompt diffs."""

from __future__ import annotations

import difflib
import re
from typing import Any

from .records import PromptRecord
from .tokens import count_tokens


HEADING_PATTERN = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")


def sections(body: str) -> dict[str, str]:
    result: dict[str, str] = {}
    current_name = "(preamble)"
    current_lines: list[str] = []
    counts: dict[str, int] = {}

    def store() -> None:
        nonlocal current_lines
        value = "\n".join(current_lines).strip()
        if value:
            result[current_name] = value
        current_lines = []

    for line in body.splitlines():
        match = HEADING_PATTERN.match(line)
        if match:
            store()
            base = match.group(1).strip()
            counts[base] = counts.get(base, 0) + 1
            current_name = base if counts[base] == 1 else f"{base} #{counts[base]}"
        else:
            current_lines.append(line)
    store()
    return result


def build_diff(left: PromptRecord, right: PromptRecord) -> dict[str, Any]:
    left_sections = sections(left.body)
    right_sections = sections(right.body)
    left_names = list(left_sections)
    right_names = list(right_sections)
    added = [name for name in right_names if name not in left_sections]
    removed = [name for name in left_names if name not in right_sections]
    changed = [name for name in left_names if name in right_sections and left_sections[name] != right_sections[name]]
    line_diff = list(
        difflib.unified_diff(
            left.body.splitlines(),
            right.body.splitlines(),
            fromfile=left.version,
            tofile=right.version,
            lineterm="",
        )
    )
    left_tokens = count_tokens(left.body)
    right_tokens = count_tokens(right.body)
    return {
        "versions": {"from": left.version, "to": right.version},
        "tokens": {"from": left_tokens, "to": right_tokens, "delta": right_tokens - left_tokens, "rule": "unicode-word-runs-and-individual-punctuation"},
        "sections": {"added": added, "removed": removed, "changed": changed},
        "line_diff": line_diff,
    }


def render_diff_markdown(data: dict[str, Any]) -> str:
    versions = data["versions"]
    tokens = data["tokens"]
    section_data = data["sections"]
    lines = [
        f"# Prompt diff: {versions['from']} to {versions['to']}",
        "",
        f"Token count: {tokens['from']} to {tokens['to']} (delta {tokens['delta']}).",
        "",
        "## Section summary",
        "",
        f"Added: {', '.join(section_data['added']) or 'none'}",
        f"Removed: {', '.join(section_data['removed']) or 'none'}",
        f"Changed: {', '.join(section_data['changed']) or 'none'}",
        "",
        "## Line diff",
        "",
        "```diff",
        *data["line_diff"],
        "```",
    ]
    return "\n".join(lines) + "\n"
