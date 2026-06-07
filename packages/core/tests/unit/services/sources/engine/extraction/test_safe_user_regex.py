# Copyright (C) 2024-2026 Chaos Cypher, Inc.
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the sandboxed user-regex wrapper (ReDoS protection)."""

from __future__ import annotations

import time

import pytest

from chaoscypher_core.services.sources.engine.extraction.safe_user_regex import (
    MAX_PATTERN_LENGTH,
    USER_REGEX_TIMEOUT,
    PatternTooLongError,
    SafeUserRegex,
    compile_safe,
)


class TestLengthCap:
    """The wrapper rejects over-long patterns before compile."""

    def test_rejects_pattern_over_512_chars(self) -> None:
        """Patterns longer than MAX_PATTERN_LENGTH raise PatternTooLongError."""
        long_pattern = "a" * (MAX_PATTERN_LENGTH + 1)
        with pytest.raises(PatternTooLongError) as excinfo:
            compile_safe(long_pattern)
        assert str(MAX_PATTERN_LENGTH) in str(excinfo.value)

    def test_accepts_pattern_at_limit(self) -> None:
        """Patterns exactly at MAX_PATTERN_LENGTH compile successfully."""
        pattern = "a" * MAX_PATTERN_LENGTH
        compiled = compile_safe(pattern)
        assert isinstance(compiled, SafeUserRegex)

    def test_invalid_regex_raises_regex_error(self) -> None:
        """Invalid regex syntax raises the underlying regex error type."""
        import regex

        with pytest.raises(regex.error):
            compile_safe("[invalid")


class TestRedosProtection:
    """The wrapper aborts catastrophic-backtracking patterns via timeout."""

    def test_redos_pattern_times_out_and_returns_false(self) -> None:
        """A known ReDoS payload (a+)+b against 30 'a's must abort under 1s."""
        # Canonical catastrophic-backtracking pattern + non-matching input.
        # With stdlib re this hangs; with regex + timeout it returns False.
        compiled = compile_safe(r"(a+)+b")
        payload = "a" * 30
        start = time.monotonic()
        result = compiled.search(payload)
        elapsed = time.monotonic() - start
        assert result is False
        # Should bail out within a few multiples of USER_REGEX_TIMEOUT (100 ms).
        assert elapsed < 1.0, f"Took {elapsed:.3f}s — timeout not enforced"

    def test_timeout_constant_is_100_ms(self) -> None:
        """USER_REGEX_TIMEOUT is documented as 0.1s (100 ms)."""
        assert USER_REGEX_TIMEOUT == 0.1

    def test_findall_also_timeout_protected(self) -> None:
        """Findall on a ReDoS payload returns an empty list instead of hanging."""
        compiled = compile_safe(r"(a+)+b")
        payload = "a" * 30
        start = time.monotonic()
        result = compiled.findall(payload)
        elapsed = time.monotonic() - start
        assert result == []
        assert elapsed < 1.0

    def test_timeout_actually_fires_and_returns_false(self) -> None:
        """A pattern that forces the regex engine past the timeout returns False.

        The canonical ``(a+)+b`` case above doesn't push the regex module
        past 100 ms on 30 chars (regex is resistant to that specific shape),
        so this extra test uses a pathological pattern that DOES trigger
        the TimeoutError path inside SafeUserRegex.search. Proves the
        wrapper swallows the timeout and returns False rather than raising.
        """
        # (a?){N}a{N} against 'a' * N is a classic exponential-cost shape;
        # at N=400 the regex module takes well over 100 ms.
        n = 400
        compiled = compile_safe(rf"(a?){{{n}}}a{{{n}}}")
        payload = "a" * n
        start = time.monotonic()
        result = compiled.search(payload)
        elapsed = time.monotonic() - start
        # Timeout must have fired → False is returned, no exception propagated.
        assert result is False
        # Bail-out budget: a small multiple of USER_REGEX_TIMEOUT (100 ms).
        assert elapsed < 1.0, f"Took {elapsed:.3f}s — timeout not enforced"


class TestBasicFunctionality:
    """The wrapper still behaves like a normal regex for sane inputs."""

    def test_search_returns_true_on_match(self) -> None:
        """search() returns True when the pattern matches."""
        compiled = compile_safe(r"hello")
        assert compiled.search("say hello world") is True

    def test_search_returns_false_on_no_match(self) -> None:
        """search() returns False when the pattern does not match."""
        compiled = compile_safe(r"hello")
        assert compiled.search("goodbye world") is False

    def test_findall_returns_matches(self) -> None:
        """findall() returns all non-overlapping matches."""
        compiled = compile_safe(r"\d+")
        assert compiled.findall("1 2 3 abc 4") == ["1", "2", "3", "4"]

    def test_compile_respects_default_flags(self) -> None:
        """compile_safe uses IGNORECASE|MULTILINE by default (parity with stdlib path)."""
        compiled = compile_safe(r"^hello")
        assert compiled.search("HELLO world") is True  # IGNORECASE
        assert compiled.search("first\nHELLO world") is True  # MULTILINE


class TestTimeoutCountAccumulator:
    """SafeUserRegex.timeout_count records timeout hits for quality counter wiring."""

    def test_timeout_count_starts_at_zero(self) -> None:
        """Freshly compiled wrapper has timeout_count == 0."""
        compiled = compile_safe(r"hello")
        assert compiled.timeout_count == 0

    def test_search_timeout_increments_count(self) -> None:
        """Each timeout in search() increments timeout_count by 1."""
        # (a?){N}a{N} is exponential for the regex engine; at N=400 it
        # reliably hits the 100 ms ceiling on current hardware.
        n = 400
        compiled = compile_safe(rf"(a?){{{n}}}a{{{n}}}")
        payload = "a" * n
        result = compiled.search(payload)
        assert result is False
        assert compiled.timeout_count == 1

    def test_findall_timeout_increments_count(self) -> None:
        """Each timeout in findall() increments timeout_count by 1."""
        n = 400
        compiled = compile_safe(rf"(a?){{{n}}}a{{{n}}}")
        payload = "a" * n
        result = compiled.findall(payload)
        assert result == []
        assert compiled.timeout_count == 1

    def test_multiple_timeouts_accumulate(self) -> None:
        """timeout_count accumulates across repeated timeout-triggering calls."""
        n = 400
        compiled = compile_safe(rf"(a?){{{n}}}a{{{n}}}")
        payload = "a" * n
        for _ in range(3):
            compiled.search(payload)
        assert compiled.timeout_count == 3

    def test_normal_search_does_not_increment_count(self) -> None:
        """Successful (non-timeout) search() calls leave timeout_count at 0."""
        compiled = compile_safe(r"hello")
        compiled.search("hello world")
        compiled.search("no match here")
        assert compiled.timeout_count == 0

    def test_normal_findall_does_not_increment_count(self) -> None:
        """Successful findall() calls leave timeout_count at 0."""
        compiled = compile_safe(r"\d+")
        compiled.findall("1 2 3")
        assert compiled.timeout_count == 0
