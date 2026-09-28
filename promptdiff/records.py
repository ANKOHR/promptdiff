"""Typed prompt record parsing and stable serialization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import math
import re
from typing import Any

import yaml

from .errors import PromptRecordError


VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
REQUIRED_FIELDS = ("name", "version", "author", "created", "tags")
OPTIONAL_FIELDS = ("latency",)


@dataclass(frozen=True)
class PromptRecord:
    name: str
    version: str
    author: str
    created: str
    tags: tuple[str, ...]
    body: str
    latency: dict[str, float] | None = None

    def metadata(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "version": self.version,
            "author": self.author,
            "created": self.created,
            "tags": list(self.tags),
        }
        if self.latency is not None:
            result["latency"] = {
                "base_ms": self.latency["base_ms"],
                "ms_per_100_tokens": self.latency["ms_per_100_tokens"],
            }
        return result


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PromptRecordError(f"field '{field}' must be a non-empty string")
    return value.strip()


def _parse_latency(value: Any) -> dict[str, float] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise PromptRecordError("field 'latency' must be a mapping")
    expected = {"base_ms", "ms_per_100_tokens"}
    if set(value) != expected:
        raise PromptRecordError("field 'latency' must contain base_ms and ms_per_100_tokens")
    parsed: dict[str, float] = {}
    for key in sorted(expected):
        number = value[key]
        if isinstance(number, bool) or not isinstance(number, (int, float)):
            raise PromptRecordError(f"field 'latency.{key}' must be a finite non-negative number")
        number = float(number)
        if not math.isfinite(number) or number < 0:
            raise PromptRecordError(f"field 'latency.{key}' must be a finite non-negative number")
        parsed[key] = number
    return parsed


def parse_record(text: str) -> PromptRecord:
    """Parse and validate a record with YAML frontmatter and a Markdown body."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines()
    if not lines or lines[0].strip() != "---":
        raise PromptRecordError("record must start with YAML frontmatter delimiter '---'")
    closing = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if closing is None:
        raise PromptRecordError("record is missing closing YAML frontmatter delimiter '---'")
    frontmatter = "\n".join(lines[1:closing])
    try:
        metadata = yaml.safe_load(frontmatter)
    except yaml.YAMLError as exc:
        raise PromptRecordError(f"malformed YAML frontmatter: {exc.__class__.__name__}") from exc
    if not isinstance(metadata, dict):
        raise PromptRecordError("YAML frontmatter must be a mapping")
    missing = [field for field in REQUIRED_FIELDS if field not in metadata]
    if missing:
        raise PromptRecordError(f"missing required field(s): {', '.join(missing)}")
    unknown = sorted(set(metadata) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS))
    if unknown:
        raise PromptRecordError(f"unknown field(s): {', '.join(str(item) for item in unknown)}")

    name = _nonempty_string(metadata["name"], "name")
    version = _nonempty_string(metadata["version"], "version")
    if not VERSION_PATTERN.fullmatch(version):
        raise PromptRecordError("field 'version' must use 1 to 64 letters, digits, dots, underscores, or hyphens and start with a letter or digit")
    author = _nonempty_string(metadata["author"], "author")
    created_value = metadata["created"]
    if isinstance(created_value, datetime):
        raise PromptRecordError("field 'created' must be an ISO date in YYYY-MM-DD form")
    if isinstance(created_value, date):
        created = created_value.isoformat()
    elif isinstance(created_value, str):
        created = created_value.strip()
        try:
            date.fromisoformat(created)
        except ValueError as exc:
            raise PromptRecordError("field 'created' must be an ISO date in YYYY-MM-DD form") from exc
    else:
        raise PromptRecordError("field 'created' must be an ISO date in YYYY-MM-DD form")
    tags_value = metadata["tags"]
    if not isinstance(tags_value, list) or any(not isinstance(tag, str) or not tag.strip() for tag in tags_value):
        raise PromptRecordError("field 'tags' must be a list of non-empty strings")
    tags = tuple(tag.strip() for tag in tags_value)
    body = "\n".join(lines[closing + 1:]).strip("\n")
    if not body.strip():
        raise PromptRecordError("prompt body must not be empty")
    return PromptRecord(
        name=name,
        version=version,
        author=author,
        created=created,
        tags=tags,
        body=body,
        latency=_parse_latency(metadata.get("latency")),
    )


def serialize_record(record: PromptRecord) -> str:
    """Serialize a validated prompt record with stable field ordering."""
    metadata = record.metadata()
    frontmatter = yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True, default_flow_style=False).rstrip()
    return f"---\n{frontmatter}\n---\n{record.body.rstrip()}\n"
