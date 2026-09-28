"""Deterministic lexical token-count heuristic shared by ledger and diffs."""

from __future__ import annotations

import re


TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", flags=re.UNICODE)


def count_tokens(text: str) -> int:
    """Count Unicode word runs and individual non-whitespace punctuation marks."""
    return len(TOKEN_PATTERN.findall(text))
