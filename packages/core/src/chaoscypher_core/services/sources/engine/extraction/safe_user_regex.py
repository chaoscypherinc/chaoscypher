# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Sandboxed regex wrapper for user-supplied patterns.

Defends against two classes of attack on ``content_exclusions.custom_patterns``:

1. **Pattern-size DoS:** a 10 MB regex handed in via a domain ``.jsonld`` file
   exhausts memory at compile time. ``MAX_PATTERN_LENGTH`` (512 chars) is
   enforced before any compile call.
2. **Catastrophic backtracking (ReDoS):** patterns such as ``(a+)+b`` against
   ``"a" * N`` inputs exhibit exponential match time under stdlib ``re``. The
   third-party ``regex`` module accepts a ``timeout=`` parameter per match
   operation; we default to ``USER_REGEX_TIMEOUT`` (100 ms). On timeout the
   wrapper returns a non-matching result instead of propagating — extraction
   must never hang on a single bad pattern.

This module is the ONLY boundary at which user-supplied regex text crosses
into the extraction pipeline. Built-in categories in ``content_categories.py``
are developer-authored and stay on stdlib ``re``.
"""

from __future__ import annotations

import regex  # type: ignore[import-untyped]
import structlog

from chaoscypher_core.exceptions import ValidationError


logger = structlog.get_logger(__name__)

__all__ = [
    "MAX_PATTERN_LENGTH",
    "USER_REGEX_TIMEOUT",
    "PatternTooLongError",
    "SafeUserRegex",
    "compile_safe",
]


MAX_PATTERN_LENGTH = 512
"""Maximum allowed length of a user regex pattern string, in characters."""

USER_REGEX_TIMEOUT = 0.1
"""Per-match-operation timeout, in seconds (100 ms)."""


class PatternTooLongError(ValidationError, ValueError):
    """Raised when a user regex exceeds ``MAX_PATTERN_LENGTH``.

    Multiply inherits from ValidationError (so the HTTP error mapper
    produces a structured envelope) and ValueError (so legacy
    ``except ValueError`` handlers keep catching it).
    """

    def __init__(self, message: str) -> None:
        """Initialize with a human-readable message.

        Args:
            message: Description of why the pattern is too long.
        """
        ValidationError.__init__(self, message, field="pattern")


class SafeUserRegex:
    """Wrapper around a compiled ``regex`` pattern that enforces a match timeout.

    Presents a ``re.Pattern``-shaped subset (search, findall, finditer) but
    every call is sandboxed with ``timeout=USER_REGEX_TIMEOUT``. On timeout
    the call is logged at WARNING and the safe default is returned (``False``
    for search, ``[]`` for findall).

    Every timeout also increments :attr:`timeout_count` so callers can read the
    accumulated hit count after a batch of match operations and feed it into a
    quality counter without needing a callback on every individual call.

    Attributes:
        pattern: The compiled ``regex`` object.
        pattern_source: The original pattern string (for logging).
        timeout_count: Number of match calls that hit the timeout since this
            instance was created (or since last reset). Incremented atomically
            on each ``TimeoutError`` in :meth:`search` and :meth:`findall`.
    """

    def __init__(self, compiled: regex.Pattern[str], pattern_source: str) -> None:
        """Wrap a pre-compiled ``regex`` pattern.

        Args:
            compiled: Already-compiled ``regex.Pattern``.
            pattern_source: Original pattern text, retained for log context.
        """
        self.pattern = compiled
        self.pattern_source = pattern_source
        self.timeout_count: int = 0

    def search(self, text: str) -> bool:
        """Return True iff the pattern matches anywhere in *text* (timeout-safe).

        Args:
            text: Input string to match against.

        Returns:
            True on match, False on no-match OR on timeout. Increments
            :attr:`timeout_count` on timeout.
        """
        try:
            return self.pattern.search(text, timeout=USER_REGEX_TIMEOUT) is not None
        except TimeoutError:
            self.timeout_count += 1
            logger.warning(
                "user_regex_timeout",
                operation="search",
                pattern=self.pattern_source[:80],
                input_length=len(text),
            )
            return False

    def findall(self, text: str) -> list[str]:
        """Return all non-overlapping matches as strings (timeout-safe).

        Args:
            text: Input string to match against.

        Returns:
            List of matched substrings, or ``[]`` on timeout. Increments
            :attr:`timeout_count` on timeout.
        """
        try:
            return list(self.pattern.findall(text, timeout=USER_REGEX_TIMEOUT))
        except TimeoutError:
            self.timeout_count += 1
            logger.warning(
                "user_regex_timeout",
                operation="findall",
                pattern=self.pattern_source[:80],
                input_length=len(text),
            )
            return []


def compile_safe(
    pattern: str,
    flags: int = regex.IGNORECASE | regex.MULTILINE,
) -> SafeUserRegex:
    """Compile a user-supplied regex with length-cap + timeout sandboxing.

    Args:
        pattern: Raw regex string from the user.
        flags: ``regex`` module flags (defaults to IGNORECASE|MULTILINE for
            parity with the pre-existing ``compile_custom_patterns`` behavior).

    Returns:
        A ``SafeUserRegex`` wrapper.

    Raises:
        PatternTooLongError: If the pattern exceeds ``MAX_PATTERN_LENGTH``.
        regex.error: If the pattern fails to compile.
    """
    if len(pattern) > MAX_PATTERN_LENGTH:
        msg = (
            f"User regex pattern exceeds {MAX_PATTERN_LENGTH}-char limit (got {len(pattern)} chars)"
        )
        raise PatternTooLongError(msg)
    compiled = regex.compile(pattern, flags)
    return SafeUserRegex(compiled, pattern)
