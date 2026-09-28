"""Named errors raised by PromptDiff."""


class PromptDiffError(Exception):
    """Base error that the command line interface can present to users."""


class PromptRecordError(PromptDiffError):
    """A prompt record has invalid frontmatter or body content."""


class StoreError(PromptDiffError):
    """The local prompt store cannot complete the requested operation."""


class FixtureError(PromptDiffError):
    """A fixture set is missing or malformed."""


class EvaluationError(PromptDiffError):
    """An evaluation rule is invalid or cannot be evaluated."""
