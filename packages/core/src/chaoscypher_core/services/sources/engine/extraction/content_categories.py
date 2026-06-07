# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Central Content Categories for Pre-Extraction Filtering.

Defines 15 built-in content categories used to detect and strip non-essential
content from document chunks before LLM entity extraction.  Each category has a
detection pattern, a matching mode (line_ratio or count), and a threshold.

Domain ``.jsonld`` files reference categories by name.  The orchestration layer
resolves names to compiled ``CategoryMatcher`` instances and applies them to
chunk content in memory — original database chunks are never modified.

Public API:
    CategoryMatcher: Dataclass encapsulating a single detection rule.
    CONTENT_CATEGORIES: Dict of all 15 built-in category matchers.
    resolve_categories: Look up built-in categories by name.
    compile_custom_patterns: Compile domain-specific custom patterns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import structlog

from chaoscypher_core.exceptions import ValidationError
from chaoscypher_core.services.sources.engine.extraction.safe_user_regex import (
    PatternTooLongError,
    SafeUserRegex,
    compile_safe,
)


logger = structlog.get_logger(__name__)

__all__ = [
    "CONTENT_CATEGORIES",
    "CategoryMatcher",
    "UnknownContentCategoryError",
    "compile_custom_patterns",
    "resolve_categories",
    "validate_custom_patterns",
]


# ---------------------------------------------------------------------------
# CategoryMatcher dataclass
# ---------------------------------------------------------------------------


@dataclass
class CategoryMatcher:
    """A single content detection rule used for pre-extraction filtering.

    Attributes:
        name: Identifier referenced by domain files.
        description: Human-readable explanation of what the category detects.
        mode: Detection mode — ``"line_ratio"`` strips matching lines,
            ``"count"`` excludes the entire chunk when threshold is met.
        pattern: Compiled regex pattern for detection.
        threshold: Trigger value — minimum ratio for line_ratio mode,
            minimum match count for count mode.
    """

    name: str
    description: str
    mode: str
    pattern: re.Pattern[str] | SafeUserRegex
    threshold: float

    def matches(self, text: str) -> bool:
        """Check whether the text matches this category's detection rule.

        Args:
            text: The chunk content to evaluate.

        Returns:
            True if the text matches according to the mode and threshold.
        """
        if self.mode == "line_ratio":
            return self._check_line_ratio(text)
        return self._check_count(text)

    def strip_lines(self, text: str) -> str:
        """Remove lines matching the pattern from the text.

        Only applies to ``line_ratio`` mode matchers.  Count mode matchers
        return the original text unchanged since they operate on the entire
        chunk rather than individual lines.

        Args:
            text: The chunk content to strip.

        Returns:
            Text with matching lines removed (line_ratio mode) or the
            original text unchanged (count mode).
        """
        if self.mode != "line_ratio":
            return text
        kept = [line for line in text.splitlines() if not self.pattern.search(line)]
        return "\n".join(kept)

    def _check_line_ratio(self, text: str) -> bool:
        """Check whether matching lines exceed the threshold ratio.

        Splits text into non-empty lines, counts how many match the pattern,
        and returns whether the ratio meets or exceeds the threshold.

        Args:
            text: The chunk content to evaluate.

        Returns:
            True if the ratio of matching lines >= threshold.
        """
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return False
        matching = sum(1 for line in lines if self.pattern.search(line))
        return matching / len(lines) >= self.threshold

    def _check_count(self, text: str) -> bool:
        """Check whether the pattern appears enough times in the text.

        Uses ``pattern.findall()`` and checks whether the count meets or
        exceeds the threshold.  Special case: if the category is
        ``"boilerplate"``, short content (< 100 chars stripped) is
        automatically matched.

        Args:
            text: The chunk content to evaluate.

        Returns:
            True if match count >= threshold (or short content for boilerplate).
        """
        if self.name == "boilerplate" and len(text.strip()) < 100:
            return True
        return len(self.pattern.findall(text)) >= self.threshold

    def match_and_strip(self, text: str) -> tuple[bool, str]:
        """Check match and strip in a single pass.

        Combines ``matches()`` and ``strip_lines()`` to avoid calling
        ``.splitlines()`` twice on the same text. For ``count`` mode,
        returns empty string on match. For ``line_ratio`` mode, splits
        lines once and both checks the ratio and builds the stripped
        result.

        Args:
            text: The chunk content to evaluate.

        Returns:
            Tuple of (matched, stripped_text). When matched is False,
            stripped_text is the original text unchanged.
        """
        if self.mode != "line_ratio":
            matched = self._check_count(text)
            return matched, ("" if matched else text)

        all_lines = text.splitlines()
        non_empty = [line for line in all_lines if line.strip()]
        if not non_empty:
            return False, text

        matching_count = sum(1 for line in non_empty if self.pattern.search(line))
        matched = matching_count / len(non_empty) >= self.threshold

        if not matched:
            return False, text

        kept = [line for line in all_lines if not self.pattern.search(line)]
        return True, "\n".join(kept)


class UnknownContentCategoryError(ValidationError, KeyError):
    """Raised when a domain references a category name that is not registered.

    Multiply inherits from ValidationError (so the HTTP error mapper
    produces a structured envelope) and KeyError (so legacy
    ``except KeyError`` handlers keep catching it).

    Attributes:
        unknown: Category names that failed to resolve.
        available: Set of all valid category names at the time of failure.
    """

    def __init__(self, unknown: list[str], available: set[str]) -> None:
        """Record unknown names and the valid set for diagnostic logging.

        Args:
            unknown: Names that were not found in ``CONTENT_CATEGORIES``.
            available: The full set of registered category names.
        """
        self.unknown = list(unknown)
        self.available = set(available)
        msg = (
            f"Unknown content category names: {self.unknown!r}. "
            f"Available: {sorted(self.available)!r}"
        )
        ValidationError.__init__(
            self,
            msg,
            details={
                "unknown": list(unknown),
                "available": sorted(available),
            },
        )


# ---------------------------------------------------------------------------
# Built-in content categories
# ---------------------------------------------------------------------------

CONTENT_CATEGORIES: dict[str, CategoryMatcher] = {
    "toc": CategoryMatcher(
        name="toc",
        description="Table of contents, navigation listings",
        mode="line_ratio",
        pattern=re.compile(r"^\s*[-*\u2022]\s+\S"),
        threshold=0.70,
    ),
    "changelog": CategoryMatcher(
        name="changelog",
        description="Version notes, what's new, errata",
        mode="count",
        pattern=re.compile(
            r"(?:changed|new|deprecated|added|removed) (?:in |since )(?:version |python )\d",
            re.IGNORECASE,
        ),
        # Tuned via test_changelog_* fixtures. 3 fired on tutorials that
        # reference multiple versions as context; 5 requires dense
        # version-change signal characteristic of actual changelogs.
        threshold=5,
    ),
    "legal": CategoryMatcher(
        name="legal",
        description="Copyright, license, terms of service, disclaimers",
        mode="count",
        pattern=re.compile(
            r"copyright|permission is hereby granted|all rights reserved"
            r"|without warranty|as[\- ]is|terms of (?:service|use)",
            re.IGNORECASE,
        ),
        threshold=2,
    ),
    "bibliography": CategoryMatcher(
        name="bibliography",
        description="References, citations, works cited",
        mode="count",
        pattern=re.compile(
            r"^\s*\[\d{1,4}\]\s+|(?:references|bibliography|works\s+cited)\s*$",
            re.IGNORECASE | re.MULTILINE,
        ),
        threshold=3,
    ),
    "acknowledgments": CategoryMatcher(
        name="acknowledgments",
        description="Dedications, prefaces, about the author, grants",
        mode="count",
        pattern=re.compile(
            r"(?:thank|grateful|gratitude|acknowledge|dedication|indebted)",
            re.IGNORECASE,
        ),
        threshold=3,
    ),
    "boilerplate": CategoryMatcher(
        name="boilerplate",
        description="Formatting artifacts, separators, stubs",
        mode="count",
        pattern=re.compile(r"^[\s]*[=\-~_*#]{3,}\s*$", re.MULTILINE),
        threshold=5,
    ),
    "metadata": CategoryMatcher(
        name="metadata",
        description="Front matter, revision history, document properties, timestamps",
        mode="count",
        pattern=re.compile(
            r"(?:last\s+(?:modified|updated|changed)"
            r"|generated\s+(?:by|on)"
            r"|revision\s+history"
            r"|build:\s*v?\d)",
            re.IGNORECASE,
        ),
        threshold=3,
    ),
    "code_blocks": CategoryMatcher(
        name="code_blocks",
        description="Source code, config snippets, shell commands",
        mode="line_ratio",
        pattern=re.compile(
            r"^\s{4,}\S|^\t\S|^```"
            r"|^\s*(?:import |from |def |class |function |const |let |var "
            r"|return |if |for |while )[a-zA-Z]",
        ),
        # Tuned via test_code_blocks_* fixtures. 0.60 stripped tutorials
        # and README-style docs that embed code inline; 0.75 requires a
        # genuinely code-dominant chunk.
        threshold=0.75,
    ),
    "data_tables": CategoryMatcher(
        name="data_tables",
        description="Tabular data, numerical dumps, statistical output",
        mode="line_ratio",
        pattern=re.compile(
            r"^\s*\|.*\|.*\||^\s*[-:]+\|[-:]+|^\s*[+][-]+[+]",
        ),
        threshold=0.50,
    ),
    "math": CategoryMatcher(
        name="math",
        description="Equations, formulas, LaTeX",
        mode="count",
        pattern=re.compile(
            r"\$\$"
            r"|\\begin\{(?:equation|align|gather)\}"
            r"|\\frac|\\sum|\\int|\\partial|\\nabla",
        ),
        threshold=3,
    ),
    "api_tables": CategoryMatcher(
        name="api_tables",
        description="Auto-generated parameter tables, HTTP status listings",
        mode="count",
        pattern=re.compile(
            r"^\s*[1-5]\d{2}\s+(?:OK|Created|Accepted|Bad Request|Unauthorized"
            r"|Forbidden|Not Found|Internal Server Error)"
            r"|(?:parameters?|arguments?)\s*:\s*$",
            re.IGNORECASE | re.MULTILINE,
        ),
        threshold=3,
    ),
    "procedural": CategoryMatcher(
        name="procedural",
        description="Installation steps, setup instructions, click-by-click how-to",
        mode="count",
        pattern=re.compile(
            r"^\s*\d+[.)]\s*(?:click|select|choose|open|navigate"
            r"|install|download|run|restart|configure)\b",
            re.IGNORECASE | re.MULTILINE,
        ),
        threshold=5,
    ),
    "advertising": CategoryMatcher(
        name="advertising",
        description="Marketing copy, CTAs, promotional content",
        mode="count",
        pattern=re.compile(
            r"subscribe|sign\s*up|try\s+(?:it\s+)?free"
            r"|download\s+now|follow\s+us|affiliate|sponsored",
            re.IGNORECASE,
        ),
        threshold=3,
    ),
    "web_artifacts": CategoryMatcher(
        name="web_artifacts",
        description="Cookie banners, consent text, navigation chrome",
        mode="count",
        pattern=re.compile(
            r"we\s+use\s+cookies|cookie\s+(?:policy|settings|consent)"
            r"|accept\s+(?:all\s+)?cookies|gdpr"
            r"|by\s+(?:continuing|using).*agree",
            re.IGNORECASE,
        ),
        threshold=2,
    ),
    "bulk_lists": CategoryMatcher(
        name="bulk_lists",
        description="Long enumeration lists without narrative context",
        mode="line_ratio",
        pattern=re.compile(r"^\s*[-*+]\s+.{3,50}$"),
        # Tuned via test_bulk_lists_* fixtures. 0.70 false-positived on
        # glossaries and feature lists embedded in prose; 0.85 requires a
        # genuinely list-heavy chunk.
        threshold=0.85,
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve_categories(names: list[str]) -> list[CategoryMatcher]:
    """Look up built-in categories by name.

    Args:
        names: List of category names to resolve (e.g. ``["toc", "legal"]``).

    Returns:
        List of ``CategoryMatcher`` instances in the same order as *names*.

    Raises:
        UnknownContentCategoryError: If any name is not found in
            ``CONTENT_CATEGORIES``. The error carries the full list of
            unknown names (not just the first) and the available set.
    """
    unknown = [n for n in names if n not in CONTENT_CATEGORIES]
    if unknown:
        raise UnknownContentCategoryError(unknown, set(CONTENT_CATEGORIES))
    return [CONTENT_CATEGORIES[name] for name in names]


def compile_custom_patterns(patterns: list[dict[str, Any]]) -> list[CategoryMatcher]:
    """Compile domain-specific custom patterns into matchers.

    Each pattern dict must contain:
        - ``regex``: Regular expression string (max 512 chars).
        - ``mode``: ``"count"`` or ``"line_ratio"``.
        - ``threshold``: Numeric trigger value.
        - ``description``: Human-readable description.

    User-supplied patterns are compiled through
    :func:`safe_user_regex.compile_safe`, which enforces a length cap and
    wraps runtime matching in a 100 ms timeout to defend against ReDoS
    (catastrophic backtracking). Patterns that exceed the length cap, fail
    to compile, or otherwise error are logged at WARNING and skipped — a
    single bad pattern must never halt extraction.

    Args:
        patterns: List of pattern definition dicts.

    Returns:
        List of compiled ``CategoryMatcher`` instances. Invalid entries
        are omitted (not raised).
    """
    matchers: list[CategoryMatcher] = []
    for i, spec in enumerate(patterns):
        raw = spec.get("regex")
        if not isinstance(raw, str):
            logger.warning("invalid_custom_pattern_missing_regex", index=i)
            continue
        try:
            compiled = compile_safe(raw)
        except PatternTooLongError as exc:
            logger.warning(
                "custom_pattern_too_long",
                index=i,
                length=len(raw),
                error=str(exc),
            )
            continue
        except Exception as exc:  # covers regex.error and any wrapper error
            logger.warning(
                "invalid_custom_pattern",
                index=i,
                regex=raw[:80],
                error=str(exc),
            )
            continue
        matchers.append(
            CategoryMatcher(
                name=f"custom_{i}",
                description=spec.get("description", ""),
                mode=spec.get("mode", "count"),
                pattern=compiled,
                threshold=float(spec.get("threshold", 1)),
            )
        )
    return matchers


def validate_custom_patterns(patterns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Eagerly validate custom pattern specs, returning per-pattern errors.

    Unlike :func:`compile_custom_patterns` (which skips bad entries silently
    at run time), this function reports every problem it finds. Intended for
    use at domain-load time so misconfiguration surfaces immediately with a
    clear error listing the offending index and regex.

    Each returned error dict has keys:
        - ``index``: 0-based position in the input list.
        - ``regex``: The offending regex string (or ``None`` if missing).
        - ``error``: Human-readable reason (length violation, compile error,
          or missing field).

    Args:
        patterns: List of pattern definition dicts to validate.

    Returns:
        List of error dicts — empty when all patterns validate cleanly.
    """
    errors: list[dict[str, Any]] = []
    for i, spec in enumerate(patterns):
        raw = spec.get("regex")
        if not isinstance(raw, str):
            errors.append(
                {
                    "index": i,
                    "regex": raw,
                    "error": "missing or non-string 'regex' field",
                }
            )
            continue
        try:
            compile_safe(raw)
        except PatternTooLongError as exc:
            errors.append({"index": i, "regex": raw, "error": str(exc)})
        except Exception as exc:  # regex.error and any other wrapper error
            errors.append({"index": i, "regex": raw, "error": str(exc)})
    return errors
